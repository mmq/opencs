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
from opencs.connlog import STREAM_GAME
from opencs.messages import (
    ClientMessage,
    DecodeError,
    ServerMessage,
    client,
    decode_client,
    server,
    server_payload,
)
from opencs.messages.limits import CHAT_STYLE_OPEN, SYSTEM_SENDER
from opencs.session.badinput import WindowedCounter
from opencs.session.phases import Phase
from opencs.session.ratelimit import TokenBucket
from opencs.wire.codec import BYTE_MIN
from opencs.wire.frame import Frame, FrameReader, build_frame
from opencs.wire.text import QuitScanner
from opencs.world.city import City
from opencs.world.seating import JoinRefused

if TYPE_CHECKING:
    from opencs.session.session import Session
    from opencs.world.progress import SavedProgress


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

    STREAM = STREAM_GAME

    def __init__(self, host: Session) -> None:
        super().__init__(host)
        self.stage = Stage.PLAYING
        self._reader = FrameReader()
        self._bad = WindowedCounter(policy.MAX_BAD_INPUT, policy.BAD_INPUT_WINDOW)
        self._bad_seen = 0
        # Budgets for the messages the city relays to everyone (policy.RELAY_LIMIT_*).
        now = host.now()
        self._relay_buckets: dict[type[ClientMessage], TokenBucket] = {
            client.PodState: TokenBucket(*policy.RELAY_LIMIT_POD_STATE, now),
            client.Shot: TokenBucket(*policy.RELAY_LIMIT_SHOT, now),
            client.HitReport: TokenBucket(*policy.RELAY_LIMIT_HIT, now),
            client.Chat: TokenBucket(*policy.RELAY_LIMIT_CHAT, now),
            client.Grenade: TokenBucket(*policy.RELAY_LIMIT_GRENADE, now),
            client.TowerOrder: TokenBucket(*policy.RELAY_LIMIT_ORDER, now),
            client.ModuleOrder: TokenBucket(*policy.RELAY_LIMIT_ORDER, now),
            client.EffectRequest: TokenBucket(*policy.RELAY_LIMIT_EFFECT, now),
            client.AirSupport: TokenBucket(*policy.RELAY_LIMIT_EFFECT, now),
            client.PickupRequest: TokenBucket(*policy.RELAY_LIMIT_MODULE_OPS, now),
            client.EjectRequest: TokenBucket(*policy.RELAY_LIMIT_MODULE_OPS, now),
        }
        self._frame_bucket = TokenBucket(*policy.FRAME_LIMIT, now)
        #: Messages dropped for exceeding their relay budget: a tally, not bad input (see
        #: ``handle``).
        self.relay_drops = 0
        self._stalled_since: float | None = None
        self._city: City | None = None
        self._player_id = -1
        # The 'B' and its repeat until the client's first frame.
        self._hello: server.Session | None = None
        self._hello_size = 0
        self._hello_sent = 0
        self._next_hello = 0.0
        self._first_frame = False
        #: When the last bytes arrived: a line that goes quiet for ``SILENCE_SECONDS`` after the
        #: first frame is dropped (II §13 item 1).
        self._last_bytes_at = now
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
        persistence = self.host.services.persistence
        saved: SavedProgress | None = None
        key: str | None = None
        try:
            if member.account is not None:  # guests are not saved (II §11.3)
                saved, key = persistence.load(member.key), member.key
        except Exception:
            # Without the stored record the player would start from the defaults and then overwrite
            # it: keep nothing for this session instead (I §9.2.11, II §11.3).
            self.host.log.exception("could not read the record of %s; none is kept", member.key)
            saved, key = None, None
        resume, self.host.resuming = self.host.resuming, None
        try:
            joined = city.join(
                member.callsign, member.team, now, identity=key, saved=saved, resume=resume
            )
        except Exception:
            # A bug in the world: log it with the request and close; nobody is seated (DESIGN §5).
            self.host.log.exception("city %s failed on a join", city.config.letter)
            self.stage = Stage.GONE
            self.host.close("city failed on the join")
            return
        if isinstance(joined, JoinRefused):
            # The Chamber has already told the client to launch; there is no way back but a new
            # connection.
            self.host.log.warning("city %s refused the join: %s", city.config.letter, joined.reason)
            self.stage = Stage.GONE
            self.host.close(f"city refused the join: {joined.reason}")
            return
        self._city, self._player_id = city, joined.player_id
        persistence.flush(city)  # the new game rolled the last SCORE into the career
        self.host.services.router.register(city.config.letter, joined.player_id, _Sink(self))
        self.host.log.info(
            "seated as player %d, team %d index %d%s",
            joined.player_id,
            joined.team,
            joined.index,
            "; resuming" if resume is not None and key is not None else "",
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
        try:
            router.deliver(city, city.leave(self._player_id, self.host.now()))
            self.host.services.persistence.flush(city)
        except Exception:
            # The player is out of this session either way; the others' notice is lost (DESIGN §5).
            self.host.log.exception(
                "city %s failed on leave of player %d", city.config.letter, self._player_id
            )

    def closed(self) -> None:
        """The socket ended. While playing, with the server not the one that closed it, that is a
        line drop (II §13 item 1). A close the server made for bad input still runs the fight
        test, since hanging up must not dodge a death, but leaves nothing to resume. A removal for
        shutdown or by an admin never gets here: it goes through ``remove``, which leaves the city
        cleanly first (our rule for a server-side close in a fight; see I §5.5 (unprompted Quit), II
        §13). After a quit the player is already out."""
        reason = self.host.close_reason
        if self.stage is Stage.PLAYING and reason is None:
            self._line_dropped(self.host.now())
        elif self.stage is Stage.PLAYING:
            self._line_dropped(self.host.now(), by_server=True)
        else:
            self._leave_city()
        self.stage = Stage.GONE

    def _line_dropped(self, fight_at: float, *, by_server: bool = False) -> None:
        """The line dropped mid-game (II §13): the city decides whether it was a fight, which
        is a death, and what record the player leaves; only a real account is held for
        ``/RESUME`` (D1). ``fight_at`` is when the line went quiet. The record is already saved
        from the reports so far. ``by_server``: the server closed the connection, so the fight
        test runs but no record is made."""
        city = self._city
        if city is None:
            return
        self._city = None
        services = self.host.services
        member = self.host.member
        account = member is not None and member.account is not None and not by_server
        # A source already holding its share of slots holds no more (MAX_HELD_PER_ADDRESS).
        crowded = (
            account
            and member is not None
            and not services.held.has_room(self.host.address, member.key)
        )
        services.router.unregister(city.config.letter, self._player_id)
        try:
            dropped = city.line_drop(
                self._player_id,
                self.host.now(),
                fight_at=fight_at,
                hold=account and not crowded,
                keep_record=not by_server,
            )
            services.router.deliver(city, dropped.events)
            services.persistence.flush(city)
        except Exception:
            # A bug in the world: the player must still be gone from the city (DESIGN §5).
            self.host.log.exception("city %s failed on a line drop", city.config.letter)
            city.leave(self._player_id, self.host.now())
            return
        record = dropped.record
        if by_server:
            self.host.log.info("closed by the server; the fight test ran, no game is held")
        elif record is None:
            self.host.log.info("line dropped; nothing is kept for this player")
        elif not account:
            self.host.log.info("line dropped; JOINGENIE cannot resume (%s)", record.reason or "ok")
        elif crowded and record.eligible:
            self.host.log.warning(
                "line dropped; nothing held: %s already holds %d games",
                self.host.address,
                policy.MAX_HELD_PER_ADDRESS,
            )
        else:
            services.held.hold(record, self.host.address)
            self.host.log.info(
                "line dropped; %s",
                "held for /RESUME" if record.eligible else f"counted as a death ({record.reason})",
            )

    # --- bytes in ----------------------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        now = self.host.now()
        self._last_bytes_at = now
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
            if not self._frame_bucket.take(now):
                failed += 1  # over the frame rate: dropped undecoded, counted as bad input
                continue
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
                bucket = self._relay_buckets.get(type(message))
                if bucket is not None and not bucket.take(self.host.now()):
                    # Dropped, but not bad input: an honest client's backlog after a stall of up
                    # to SILENCE_SECONDS arrives at once and can exceed a budget. The budget bounds
                    # the relay; the frame bucket and decode failures are the bad-input signal.
                    self.relay_drops += 1
                    if isinstance(message, client.Shot):
                        self._note_shot(message)  # not relayed, but still a shot (II §9.3)
                    elif isinstance(message, client.TowerOrder | client.ModuleOrder):
                        self._refuse_order(message)  # a tower's point comes back (I §9.4.2)
                    return
                self._to_city(message)

    def _note_shot(self, shot: client.Shot) -> None:
        """Let the city log a shot that is dropped unrelayed (II §9.3)."""
        city = self._city
        assert city is not None
        city.note_shot(self._player_id, shot, self.host.now())

    def _refuse_order(self, order: ClientMessage) -> None:
        """Answer an over-budget order instead of leaving the client waiting: ``'O'`` 2 for a
        tower, ``'E'``-``'P'`` ok = 0 for a module."""
        city = self._city
        assert city is not None
        self.host.services.router.deliver(city, city.refuse_order(self._player_id, order))

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
        # Whatever the call changed in the player's record, even if the handler then
        # raised (the city notes progress in a ``finally``).
        self.host.services.persistence.flush(city)

    # --- time --------------------------------------------------------------------------------

    def tick(self, now: float) -> None:
        if self.stage is Stage.PLAYING:
            self._tick_playing(now)
        elif self.stage is Stage.QUITTING:
            self._tick_quitting(now)

    def _tick_playing(self, now: float) -> None:
        if self._first_frame and now - self._last_bytes_at >= policy.SILENCE_SECONDS:
            # POD.FUN sent a packet and then nothing: the line is dead though the socket is not
            # (II §13 item 1). The fight window is counted from when it went quiet.
            self.host.log.info(
                "no bytes for %.0f s; the line is taken as dropped", now - self._last_bytes_at
            )
            self._line_dropped(self._last_bytes_at)
            self.stage = Stage.GONE
            self.host.close("line silent")
            return
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

    def remove(self, notice: str) -> None:
        """The server ends the player's game: the reason on its screen as a system line, then the
        quit handshake as if the player had asked (I §5.5, I §8.5, an unprompted ``'C'``)."""
        text = server.sanitize_chat(notice.encode("ascii", "replace"))
        self.host.io.send(server.Chat(SYSTEM_SENDER, CHAT_STYLE_OPEN, text))
        self._begin_quit(self.host.now())

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
                self.host.log.info("QUIT line %d of 2", self._quits)
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
            self.host.return_to_chamber(late_quits=2 - self._quits)
