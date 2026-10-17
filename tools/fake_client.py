#!/usr/bin/env python3
"""A synthetic game client: a connection that is not DOSBox.

Built on ``opencs.wire`` and ``opencs.messages``. It drives the same
Chamber text flow the real client drives, then speaks the in-game binary protocol, and logs what
the server sends it. The log lines are what ``tools/judge.py`` greps for, so they are kept
verbatim.

What it is not: an emulator. It renders nothing, runs no pod physics and does not verify that what
it receives is correct, only that it arrives. The real client remains the only authority on
whether something looks right.

Usage (run the server first)::

    uv run python tools/fake_client.py --port 2399
    uv run python tools/fake_client.py --name BRAVO --say hi --play "B RED"
    uv run python tools/fake_client.py --lobby "/CITY,/WHO" --stop-after-launch
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import math
import time
from collections.abc import Callable

from opencs import policy
from opencs.messages import ClientMessage, WirePos, WireXZ, client, client_payload
from opencs.messages.catalog import CATALOG
from opencs.messages.limits import SYSTEM_SENDER
from opencs.wire.codec import BIAS88_MAX, BIAS88_MIN, enc_num_decode
from opencs.wire.frame import FrameReader, build_frame

HOST, PORT = "127.0.0.1", policy.DEFAULT_PORT

#: The real client reports its own state about once a second.
TELEMETRY_PERIOD = 1.0

#: Where this pod claims to be: team 0's corner cell, so two fake clients are in sight of each
#: other. Walks a slow circle so movement is obvious.
START_CELL = (0, 0)
START_OFF = (300, 300)
WALK_RADIUS = 120  # feet
WALK_PERIOD = 20.0  # seconds per lap
GROUND_Y = 10  # feet, on the ground (SPEC: observed)
FULL_POWER = 3000  # a full pod

#: How long to wait for the server to echo our quit before giving up, in seconds.
QUIT_ECHO_WAIT = 15.0


def pos(x: int, y: int, z: int) -> WirePos:
    """Where we are, in the start cell, ``x``/``z`` feet from its corner."""
    return WirePos(START_CELL[0], START_CELL[1], x, y, z)


def frame(message: ClientMessage) -> bytes:
    """A client frame: LF-terminated, as the real client ends them (I §4.1)."""
    return build_frame(client_payload(message), terminator=b"\x0a")


def clamp_b88(v: int) -> int:
    return max(BIAS88_MIN, min(BIAS88_MAX, v))


class Terminal:
    """The Chamber's text side as the client sees it: send lines, wait for what the server says.

    Bytes read past a match stay buffered: a terminal never loses them.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        quiet: bool,
        log: Callable[..., None],
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.quiet = quiet
        self.log = log
        self.pending = b""

    async def send(self, data: bytes) -> None:
        self.writer.write(data)
        await self.writer.drain()
        if not self.quiet:
            self.log("chamber -> %r", data)

    async def _fill(self, timeout: float) -> bytes:
        data = await asyncio.wait_for(self.reader.read(256), timeout=timeout)
        if not data:
            raise ConnectionError("server closed the connection")
        if not self.quiet:
            self.log("chamber <- %r", data)
        return data

    async def expect(self, *needles: bytes, timeout: float = 30.0) -> tuple[bytes, bytes]:
        """Read until one of ``needles`` shows up; return which, and the buffer from there."""
        buf = self.pending
        deadline = time.monotonic() + timeout
        while True:
            hits = [(buf.index(n), n) for n in needles if n in buf]
            if hits:
                at, n = min(hits)
                self.pending = buf[at + len(n) :]
                return n, buf[at:]
            left = deadline - time.monotonic()
            try:
                if left <= 0:
                    raise TimeoutError
                buf += await self._fill(left)
            except TimeoutError:
                raise TimeoutError(f"waiting for {needles}, got {buf!r}") from None

    async def quiet_for(self, silence: float, limit: float = 3.0) -> bytes:
        """Everything the server says until it has been quiet for ``silence`` seconds."""
        buf, self.pending = self.pending, b""
        end = time.monotonic() + limit
        while time.monotonic() < end:
            try:
                buf += await self._fill(silence)
            except TimeoutError:
                break
        return buf

    def take_pending(self) -> bytes:
        """The bytes read but not yet matched, handed to the next phase."""
        data, self.pending = self.pending, b""
        return data


def printable_lines(data: bytes) -> list[str]:
    """The displayed lines in ``data``: printable ASCII only, empty lines dropped."""
    lines = (
        "".join(ch for ch in raw if " " <= ch <= "~")
        for raw in data.decode("ascii", "replace").split("\r")
    )
    return [line for line in lines if line]


class FakeClient:
    def __init__(self, name: str, quiet: bool, say: str | None, still: bool) -> None:
        self.name = name.upper()[:8]
        self.still = still
        self.say = say
        self.quiet = quiet
        self.weight = 0  # pod weight: sum of installed module weights
        self._sub_n: dict[str, int] = {}  # 'E'-x / 'D'-x counts, see _observe
        self.orders: list[tuple[float, str]] = []  # (t, kind), see --order
        self._guppy_due: list[tuple[float, int]] = []  # (t, net_id) arrival reports to send
        self._guppy_seen: set[int] = set()
        self.reader = FrameReader()
        self.t0 = time.monotonic()
        self.rx: dict[str, int] = {}
        self.pods: dict[int, str] = {}  # net ids the server has introduced to us
        self.chat_seen: list[str] = []
        self._pending_tags: list[int] = []  # tags to echo back in 'd'
        self._pings = 0
        self.play_args = ""
        self.lobby_lines: list[str] = []
        self.resume = False
        self.quit_after: float | None = None
        self.quitting = False
        self.drop_after: float | None = None
        self.freeze_after: float | None = None
        self.stop_after_launch = False
        self.early = b""
        self.chamber_io: Terminal | None = None

    @property
    def t(self) -> float:
        return time.monotonic() - self.t0

    def log(self, msg: str, *args: object) -> None:
        print(f"[{self.t:6.1f}s] {msg % args if args else msg}", flush=True)

    # -- chamber -----------------------------------------------------------------------------
    async def chamber(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        """Drive the pre-game text flow from the client's side."""
        term = Terminal(r, w, self.quiet, self.log)
        self.chamber_io = term
        # No AT/OK exchange: the emulator's modem answers LOGON.SCR's AT lines itself, so a real
        # client's first bytes are HHH (I §5.1). Then LOGON.SCR as captured: a bare CR, the
        # account line, a pause, the literal <Not Telling!> line whatever the server says.
        await term.send(b"HHH")
        await term.expect(b"U#")
        await term.send(b"\r")
        await term.send(self.name.encode("ascii") + b",PASSWORD\r")
        await asyncio.sleep(0.3)
        await term.send(b"<Not Telling!>\r")
        await term.expect(b"*")
        # Page 1380 is the game's page (the GO button). The lobby runs the version check, then
        # gives us the Chamber ('A'); we answer like the real Chamber program.
        await term.send(b"M 1380;3\r")
        await term.expect(b"PRESS RETURN\r")
        await term.send(b"CSTTBT 105 150\r")
        await term.expect(b"\x05A\r")
        await term.expect(b"\x05D0\r")  # the welcome line's color block
        self.log("in the Chamber")
        for line in self.lobby_lines:
            await term.send(line.encode("ascii") + b"\r")
            for shown in printable_lines(await term.quiet_for(0.4)):
                self.log("lobby %s -> %s", line, shown)
        if self.resume:
            await term.send(b"/RESUME\r")
        elif self.play_args:
            await term.send(b"/PLAY " + self.play_args.encode("ascii") + b"\r")
        else:
            await term.send(b"/PLAY\r")
        _, buf = await term.expect(b"\x05E", b"\x01E")
        self.log("server chose city: %r", buf.split(b"\r")[0])
        await term.send(b"/GO 0\r")
        await term.expect(b"\x05C\r")
        # Bytes after the 'C' already belong to the game phase.
        self.early = term.take_pending()
        self.log("launched -- switching to the binary protocol")

    async def back_in_chamber(self) -> None:
        """After our quit the Chamber program comes up again; wait for the lobby's 'A'."""
        term = self.chamber_io
        assert term is not None
        await term.expect(b"\x05A\r", timeout=20.0)
        self.log("back in the Chamber after the quit")
        await term.send(b"/STATUS\r")
        await term.expect(b"\r")  # any reply: the wording is the lobby's own (II §4.2)
        await asyncio.sleep(0.5)
        await term.send(b"/QUIT\r")
        await term.expect(b"\x05B\r")
        await term.send(b"BYE\r")
        await term.expect(b"GOODBYE")
        self.log("left via /QUIT and BYE")

    # -- in-game -----------------------------------------------------------------------------
    def position(self) -> tuple[int, int, int]:
        """Where this pod claims to be, this instant: x, z and orientation."""
        if self.still:
            return START_OFF[0], START_OFF[1], 450
        ang = 2 * math.pi * (self.t % WALK_PERIOD) / WALK_PERIOD
        return (
            int(START_OFF[0] + WALK_RADIUS * math.cos(ang)),
            int(START_OFF[1] + WALK_RADIUS * math.sin(ang)),
            int((math.degrees(ang) * 10) % 3600),
        )

    def pod_state(self) -> int:
        """The state the real client reports, in its observed sequence: 4 (drop) -> 1 (falling)
        -> 2 (touchdown) -> 0 (on the ground) over about 5 s (I §8.24)."""
        if self.still:
            return 0
        if self.t < 1.0:
            return 4
        if self.t < 3.5:
            return 1
        if self.t < 4.5:
            return 2
        return 0

    def fwd_speed(self) -> int:
        """Forward speed in ft/s, matching the circle this fake walks."""
        if self.still:
            return 0
        return round(2 * math.pi * WALK_RADIUS / WALK_PERIOD)

    async def play(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> str | None:
        sender = asyncio.create_task(self._telemetry(w))
        early, self.early = self.early, b""
        quit_requested_at: float | None = None
        try:
            while True:
                if self.drop_after is not None and self.t > self.drop_after:
                    self.log("LINE DROP: closing the socket with no quit handshake")
                    return "drop"
                if self.quitting and quit_requested_at is None:
                    quit_requested_at = self.t
                if quit_requested_at is not None and self.t > quit_requested_at + QUIT_ECHO_WAIT:
                    self.log("no quit echo from the server after %.0f s", QUIT_ECHO_WAIT)
                    return None
                if early:
                    data, early = early, b""
                else:
                    try:
                        data = await asyncio.wait_for(r.read(4096), timeout=1.0)
                    except TimeoutError:
                        if self.t > 300.0:
                            raise
                        continue
                if not data:
                    self.log("server closed the connection")
                    return None
                for fr in self.reader.feed(data):
                    key = fr.type if 0x20 <= fr.payload[0] < 0x7F else f"0x{fr.payload[0]:02x}"
                    self.rx[key] = self.rx.get(key, 0) + 1
                    self._observe(key, fr.payload)
                    if key == "C" and self.quit_after is not None:
                        # The inbound 'C' is the quit echo: the real client writes QUIT\r twice
                        # raw and its game program exits back to the Chamber (I §5.5).
                        self.log("quit echoed -- sending QUIT x2, back to the Chamber")
                        w.write(b"QUIT\rQUIT\r")
                        await w.drain()
                        return "quit"
        finally:
            sender.cancel()

    async def _telemetry(self, w: asyncio.StreamWriter) -> None:
        """Report our position like the real client does, about once a second."""
        said = 0
        while True:
            if self.freeze_after is not None and self.t > self.freeze_after:
                # A dead modem: the socket stays open and nothing more is sent.
                self.log("FROZEN: sending nothing more, socket left open")
                await asyncio.sleep(3600)
            x, z, orient = self.position()
            v = clamp_b88(self.fwd_speed())
            w.write(
                frame(
                    client.PodState(
                        pos(x, GROUND_Y, z),
                        orient,
                        v,
                        v,
                        0,
                        0,
                        self.pod_state(),
                        FULL_POWER,
                        self.weight,
                        0,
                        0,
                    )
                )
            )
            await w.drain()
            if self.quit_after is not None and self.t > self.quit_after and not self.quitting:
                # Shift-Q: a bare 'C' is the request (I §5.5).
                self.quitting = True
                self.log("quitting (Shift-Q): sending bare 'C'")
                w.write(frame(client.Quit()))
                await w.drain()
            lines = self.say.split(",") if self.say else []
            if said < len(lines) and self.t > 8.0 + 3.0 * said:
                # The client renders nothing locally and waits for the server to relay (I §8).
                line = lines[said]
                said += 1
                raw = line.encode("ascii", "replace")[:0xEF]
                w.write(frame(client.Chat(0, raw)))
                await w.drain()
                self.log("said %r (expecting the server to relay it back)", line)
            for order in [o for o in self.orders if self.t >= o[0]]:
                self.orders.remove(order)
                w.write(self._order_frame(order[1], x, z))
                self.log("ordered a %s", order[1])
            # Guppy arrival reports: one 'K' per leg (I §9.4.1); we fake the flight time.
            for due in [d for d in self._guppy_due if self.t >= d[0]]:
                self._guppy_due.remove(due)
                w.write(frame(client.LegArrived(due[1])))
                self.log("reported guppy %d ARRIVED", due[1])
            # The latency ping the real client sends at startup, twice (I §8.7).
            if self._pings < 2 and self.t > 3.0:
                self._pings += 1
                w.write(frame(client.Ping()))
                self.log("sent 'E'-'N' ping, expecting an 'E'-'N' reply")
            # Flush accumulated tags as 'd' acks, as the real client batches them (I §4.2, I §8.26).
            while self._pending_tags:
                batch, self._pending_tags = self._pending_tags[:11], self._pending_tags[11:]
                w.write(frame(client.Acks(bytes(batch))))
            await w.drain()
            await asyncio.sleep(TELEMETRY_PERIOD)

    def _order_frame(self, kind: str, x: int, z: int) -> bytes:
        """The client frame for one ``--order`` kind."""
        if kind == "tower":
            return frame(client.TowerOrder(pos(x, GROUND_Y, z), 0, 0))
        if kind == "module":
            return frame(client.ModuleOrder(1, 1, 0))
        if kind == "grenade":
            return frame(client.Grenade(pos(x, GROUND_Y, z), 900, 3335))
        if kind == "A":
            return frame(client.AirSupport(0, WireXZ(START_CELL[0], START_CELL[1], x, z)))
        return frame(client.EffectRequest(kind))  # W H L C S I ...

    def _observe(self, key: str, payload: bytes) -> None:
        # Echo the tag of every uppercase packet, as the real client does via 'd' (I §4.2, I §8.26).
        if 0x41 <= ord(key[0]) <= 0x5A and len(payload) > 1:
            self._pending_tags.append(payload[1])
        handler = {
            "Q": self._on_q,
            "K": self._on_k,
            "B": self._on_b,
            "G": self._on_g,
            "A": self._on_a,
            "H": self._on_h,
            "b": self._on_relayed_state,
            "a": self._on_chat,
            "U": self._on_chat,
            "E": self._on_sub,
            "D": self._on_sub,
        }.get(key)
        if handler is not None:
            handler(key, payload)
        elif self.rx[key] <= 2:
            self.log("rx '%s' #%d %s", key, self.rx[key], payload.hex(" "))

    def _on_q(self, key: str, payload: bytes) -> None:
        if len(payload) > 2 and payload[2] - 0x10 == 5:
            # 'Q' sub 5 install: carry its weight in our 'b' like the real client.
            mod, _ = enc_num_decode(payload, 3)
            module = CATALOG.get(mod)
            self.weight += module.weight if module else 0
            self.log("installed module %d -> weight %d", mod, self.weight)
        elif self.rx[key] <= 2:
            self.log("rx '%s' #%d %s", key, self.rx[key], payload.hex(" "))

    def _on_k(self, key: str, payload: bytes) -> None:
        # A guppy create or dispatch: report arrival like the real client (at once for a create,
        # after a pretend flight for a dispatch; we cannot tell which, so 0.5 s for the first 'K'
        # per id and 3 s after).
        gid, _ = enc_num_decode(payload, 2)
        first = gid not in self._guppy_seen
        self._guppy_seen.add(gid)
        # A new Muncher (id >= 260) starts with its destination zeroed and does not report.
        if not (first and gid >= 0x104):
            self._guppy_due.append((self.t + (0.5 if first else 3.0), gid))
        if self.rx[key] <= 2:
            self.log("rx '%s' #%d %s", key, self.rx[key], payload.hex(" "))

    def _on_b(self, key: str, payload: bytes) -> None:
        # Our own session record: our net id and packed roster slot (I §8). 'B' is uppercase,
        # so the fields start at payload[2].
        if self.rx[key] == 1:
            nid, slot = payload[2] - 0x10, payload[3] - 0x10
            self.log(
                "MY 'B': net_id=%d slot=%d (team=%d index=%d)  %s",
                nid,
                slot,
                slot & 7,
                slot >> 3,
                payload.hex(" "),
            )

    def _on_g(self, key: str, payload: bytes) -> None:
        # Another player: 'G' <tag> id [len]callsign slot ...
        try:
            nid = payload[2] - 0x10
            ln = payload[3] - 0x10
            name = payload[4 : 4 + ln].decode("ascii", "replace")
            slot = payload[4 + ln] - 0x10
        except IndexError:
            self.log("'G' too short to parse: %s", payload.hex(" "))
            return
        self.pods[nid] = name
        self.log(
            "*** INTRODUCED to player %r: id=%d slot=%d (team=%d index=%d)",
            name,
            nid,
            slot,
            slot & 7,
            slot >> 3,
        )

    def _on_a(self, key: str, payload: bytes) -> None:
        nid = payload[2] - 0x10
        self.log("'A' land-pod id=%d  (%s)", nid, self.pods.get(nid, "us"))

    def _on_h(self, key: str, payload: bytes) -> None:
        nid = payload[2] - 0x10
        self.log(
            "*** 'H' REMOVE pod id=%d (%s) -- a real client frees the object here",
            nid,
            self.pods.pop(nid, "unknown to us"),
        )

    def _on_relayed_state(self, key: str, payload: bytes) -> None:
        # Another player's relayed state; lowercase, so the id is payload[1].
        n = self.rx[key]
        if n <= 3 or n % 20 == 0:
            self.log("'b' pod-state #%d id=%d %s", n, payload[1] - 0x10, payload.hex(" ")[:44])

    def _on_chat(self, key: str, payload: bytes) -> None:
        # 'a' is lowercase; 'U' is uppercase, so its fields start one byte later.
        i = 2 if key == "U" else 1
        try:
            snd = payload[i] - 0x10
            ln = payload[i + 2] - 0x10
            text = payload[i + 3 : i + 3 + ln].decode("ascii", "replace")
        except IndexError:
            self.log("chat unparsed %s", payload.hex(" "))
            return
        if snd == SYSTEM_SENDER:
            who = "system"
        elif snd in self.pods:
            who = self.pods[snd]
        else:
            who = "me (or a player we have not been introduced to)"
        self.chat_seen.append(text)
        self.log("*** CHAT from id=%d (%s): %r", snd, who, text)

    def _on_sub(self, key: str, payload: bytes) -> None:
        # Second-level types share a letter. Count each sub on its own, or one chatty sub
        # ('E'-'B') hides every other.
        if len(payload) <= 2:
            return
        if key == "E" and payload[2:3] == b"N":
            self.log(
                "*** got the 'E'-'N' PING REPLY -- the client's latency meter would show a value"
            )
        elif key == "E" and payload[2:3] == b"G":
            self.log(
                "*** rx 'E'-'G' GRENADE IN FLIGHT (another player's, relayed) %s",
                payload.hex(" "),
            )
        else:
            sub = f"{key}-{chr(payload[2])}"
            self._sub_n[sub] = self._sub_n.get(sub, 0) + 1
            if self._sub_n[sub] <= 2:
                self.log("rx '%s' #%d %s", sub, self._sub_n[sub], payload.hex(" "))

    def summary(self) -> None:
        print("=" * 72)
        print(f"fake client {self.name} -- {self.t:.1f}s")
        counts = ", ".join(f"{k}={v}" for k, v in sorted(self.rx.items()))
        print("  rx counts   " + (counts or "(nothing)"))
        print(f"  players introduced to us: {self.pods or '(none)'}")
        print(f"  chat lines seen: {self.chat_seen or '(none)'}")
        print("=" * 72)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--name", default="BRAVO", help="callsign / GEnie login (max 8 chars)")
    ap.add_argument("--port", type=int, default=PORT, help="server port")
    ap.add_argument("--host", default=HOST, help="server address")
    ap.add_argument("--still", action="store_true", help="stand still instead of walking a circle")
    ap.add_argument(
        "--say",
        help="chat ~8s in and watch for the relay; a comma list sends one line every 3 s",
    )
    ap.add_argument("--quiet", action="store_true", help="skip the chamber byte log")
    ap.add_argument("--play", default="", help="arguments for /PLAY, e.g. 'B GREEN'")
    ap.add_argument(
        "--lobby",
        default="",
        help="comma list of Chamber lines to send before /PLAY, e.g. '/CITY,/WHO,hello'",
    )
    ap.add_argument(
        "--quit-after",
        type=float,
        default=None,
        help="quit the game after N seconds and walk the return-to-Chamber path",
    )
    ap.add_argument(
        "--drop-after",
        type=float,
        default=None,
        help="close the socket N seconds into the game, no quit (a line drop)",
    )
    ap.add_argument(
        "--freeze-after",
        type=float,
        default=None,
        help="stop sending N seconds into the game but keep the socket open",
    )
    ap.add_argument("--resume", action="store_true", help="type /RESUME instead of /PLAY")
    ap.add_argument(
        "--stop-after-launch",
        action="store_true",
        help="exit as soon as the launch ('C') is received, without playing",
    )
    ap.add_argument(
        "--order",
        default="",
        help="comma list of KIND@SECONDS: tower, module, grenade, A (Air Support) or an effect "
        "letter W H L C S I, e.g. tower@15,module@16,L@20",
    )
    return ap.parse_args(argv)


async def main(argv: list[str] | None = None) -> None:
    a = parse_args(argv)
    fc = FakeClient(a.name, a.quiet, a.say, a.still)
    fc.orders = [(float(t), k) for k, t in (o.split("@") for o in a.order.split(",") if o)]
    fc.play_args, fc.quit_after = a.play, a.quit_after
    fc.drop_after, fc.resume, fc.freeze_after = a.drop_after, a.resume, a.freeze_after
    fc.lobby_lines = [x for x in a.lobby.split(",") if x]
    fc.stop_after_launch = a.stop_after_launch
    r, w = await asyncio.open_connection(a.host, a.port)
    fc.log("connected to %s:%d as %s", a.host, a.port, a.name)
    try:
        await fc.chamber(r, w)
        if fc.stop_after_launch:
            return
        ended = await fc.play(r, w)
        if ended == "drop":
            w.transport.abort()
        elif ended == "quit":
            await fc.back_in_chamber()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception as e:
        fc.log("ERROR: %s: %s", type(e).__name__, e)
    finally:
        fc.summary()
        w.close()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
