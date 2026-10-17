"""The in-game phase: the binary session of one player (I §5.5, I §4.1, I §7.1, I §7.8).

On entering it seats the player in its city and sends ``'B'`` at once, repeating it until the
client's first frame (a transport concern, so it lives here and not in the world). Inbound frames
are decoded and either answered here (the latency ping, the quit handshake) or handed to the city,
whose events the router delivers. The world never sees a socket and this phase never decides a
rule.

Quitting (I §5.5): the client's bare ``'C'`` is answered with a freshly built ``'C'``; the player
leaves its city and no more in-game packets are sent; the raw stream is scanned for ``QUIT\\r``
twice (it may share a read with the last frames); then, ``RETURN_SETTLE`` after the last one, the
Chamber's buttons come back and the connection is a Chamber session again.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import TYPE_CHECKING

from opencs import policy
from opencs.messages import (
    ClientMessage,
    DecodeError,
    ServerMessage,
    client,
    decode_client,
    server,
    server_payload,
)
from opencs.session.badinput import WindowedCounter
from opencs.session.phases import Phase
from opencs.wire.codec import BYTE_MIN
from opencs.wire.frame import Frame, FrameReader, build_frame
from opencs.wire.text import QuitScanner
from opencs.world.city import City, JoinRefused

if TYPE_CHECKING:
    from opencs.session.session import Session


class Stage(Enum):
    """Where the in-game session is."""

    PLAYING = auto()
    QUITTING = auto()  # answered 'C', left the city; waiting for QUIT, QUIT
    GONE = auto()  # refused, closed, or handed back to the Chamber


class _Sink:
    """The router's way to this player's connection: only while it is playing."""

    def __init__(self, phase: InGamePhase) -> None:
        self._phase = phase

    def send(self, msg: ServerMessage) -> None:
        if self._phase.stage is Stage.PLAYING:
            self._phase.host.io.send(msg)


class InGamePhase(Phase):
    """Binary frames in, city calls and routed events out."""

    def __init__(self, host: Session) -> None:
        super().__init__(host)
        self.stage = Stage.PLAYING
        self._reader = FrameReader()
        self._bad = WindowedCounter(policy.MAX_BAD_INPUT, policy.BAD_INPUT_WINDOW)
        self._bad_seen = 0
        self._stalled_since: float | None = None
        self._city: City | None = None
        self._player_id = -1
        # The 'B' and its repeat until the client's first frame.
        self._hello: server.Session | None = None
        self._hello_size = 0
        self._hello_sent = 0
        self._next_hello = 0.0
        self._first_frame = False
        # The quit handshake.
        self._quit_scanner = QuitScanner()
        self._quit_deadline = 0.0
        self._quits = 0
        self._first_quit_at = 0.0
        self._last_quit_at = 0.0

    # --- entering and leaving ----------------------------------------------------------------

    def enter(self) -> None:
        member = self.host.member
        assert member is not None and member.city is not None and member.team is not None
        city = self.host.services.cities[member.city]
        now = self.host.now()
        joined = city.join(member.callsign, member.team, now)
        if isinstance(joined, JoinRefused):
            # The Chamber has already told the client to launch; there is no way back but a new
            # connection.
            self.host.log.warning("city %s refused the join: %s", city.config.letter, joined.reason)
            self.stage = Stage.GONE
            self.host.close(f"city refused the join: {joined.reason}")
            return
        self._city, self._player_id = city, joined.player_id
        self.host.services.router.register(city.config.letter, joined.player_id, _Sink(self))
        self.host.log.info(
            "seated as player %d, team %d index %d", joined.player_id, joined.team, joined.index
        )
        self._hello = joined.hello
        self._hello_size = len(build_frame(server_payload(joined.hello, BYTE_MIN)))
        self._next_hello = now
        self._send_hello(now)

    def _send_hello(self, now: float) -> None:
        assert self._hello is not None
        self.host.io.send(self._hello)
        self._hello_sent += self._hello_size
        self._next_hello = max(self._next_hello + policy.B_REPEAT_INTERVAL, now)

    def _leave_city(self) -> None:
        """The player is out: stop delivering to it, free its id, tell the others (I §7.8)."""
        city = self._city
        if city is None:
            return
        self._city = None
        router = self.host.services.router
        router.unregister(city.config.letter, self._player_id)
        router.deliver(city, city.leave(self._player_id, self.host.now()))

    def closed(self) -> None:
        """A line drop mid-game (``/RESUME`` records are M10's)."""
        self._leave_city()
        self.stage = Stage.GONE

    # --- bytes in ----------------------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        now = self.host.now()
        quits = self._quit_scanner.feed(data)
        if self.stage is Stage.QUITTING:
            self._count_quits(len(quits), now)
            return
        if self.stage is not Stage.PLAYING:
            return
        frames = self._reader.feed(data)
        if frames:
            self._first_frame = True
        self._process(frames, now)
        if self._is_quitting():  # the 'C' was in this read, and maybe QUIT\r after it
            self._count_quits(len(quits), now)
            return
        # A partial frame that is not making progress starts the stall clock (see ``tick``).
        if self._reader.pending == 0 or frames:
            self._stalled_since = None
        elif self._stalled_since is None:
            self._stalled_since = now

    def _is_quitting(self) -> bool:
        return self.stage is Stage.QUITTING

    def _process(self, frames: list[Frame], now: float) -> None:
        failed = self._reader.bad_frames - self._bad_seen
        self._bad_seen = self._reader.bad_frames
        for frame in frames:
            try:
                message = decode_client(frame.payload)
            except DecodeError as exc:
                failed += 1
                self.host.log.debug("undecodable %r frame: %s", frame.type, exc)
                continue
            self.handle(message)
        if self._bad.record(now, failed):
            self.host.log.warning(
                "%d bad frames or messages in %.0f s; closing",
                self._bad.total,
                policy.BAD_INPUT_WINDOW,
            )
            self.host.close("too much bad input")

    def handle(self, message: ClientMessage) -> None:
        """One decoded message. Anything after the quit began is dropped."""
        if self.stage is not Stage.PLAYING:
            return
        match message:
            case client.Ping():
                # Reply at once, ahead of queued traffic: the client measures the delay (I §8.7).
                self.host.io.send_first(server.PingReply())
            case client.Quit():
                self._begin_quit(self.host.now())
            case client.Acks() | client.Flags():
                pass  # acknowledgments; the client's own 'S' is not relayed (I §8)
            case _:
                self._to_city(message)

    def _to_city(self, message: ClientMessage) -> None:
        """Hand a message to the world. An exception in the city is a bug: log it with the
        request, drop the request, keep the connection and the city running (DESIGN §5)."""
        city = self._city
        assert city is not None
        try:
            events = city.client_message(self._player_id, message, self.host.now())
            self.host.services.router.deliver(city, events)
        except Exception:
            self.host.log.exception(
                "city %s failed on %r; request dropped", city.config.letter, message
            )

    # --- time --------------------------------------------------------------------------------

    def tick(self, now: float) -> None:
        if self.stage is Stage.PLAYING:
            self._tick_playing(now)
        elif self.stage is Stage.QUITTING:
            self._tick_quitting(now)

    def _tick_playing(self, now: float) -> None:
        if (
            not self._first_frame
            and now >= self._next_hello
            and self._hello_sent + self._hello_size <= policy.B_REPEAT_CAP_BYTES
        ):
            self._send_hello(now)
        # A header whose frame never completed must not hold back real frames for ever
        # (our partial-frame timeout).
        since = self._stalled_since
        if since is not None and now - since >= policy.FRAME_STALL_SECONDS:
            self.host.log.debug(
                "abandoning a stalled partial frame (%d bytes)", self._reader.pending
            )
            self._process(self._reader.give_up_pending(), now)
            self._stalled_since = now if self._reader.pending else None

    # --- the quit handshake ------------------------------------------------------------------

    def _begin_quit(self, now: float) -> None:
        # Built as a normal uppercase message, tag assigned at the send boundary; never an echo of
        # the client's frame (I §5.5 step 2).
        self.host.io.send(server.QuitAck())
        self.stage = Stage.QUITTING
        self._leave_city()
        self._quit_deadline = now + policy.QUIT_WAIT
        self.host.log.info("quit requested; waiting for QUIT")

    def _count_quits(self, found: int, now: float) -> None:
        for _ in range(found):
            if self._quits < 2:
                self._quits += 1
                self._last_quit_at = now
                if self._quits == 1:
                    self._first_quit_at = now

    def _tick_quitting(self, now: float) -> None:
        if self._quits == 0:
            if now >= self._quit_deadline:
                self.stage = Stage.GONE
                self.host.close("no QUIT after the quit handshake")
            return
        second_over = self._quits >= 2 or now >= self._first_quit_at + policy.QUIT_SECOND_WAIT
        if second_over and now >= self._last_quit_at + policy.RETURN_SETTLE:
            self.stage = Stage.GONE
            self.host.return_to_chamber()
