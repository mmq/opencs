import asyncio
import contextlib
import logging
import socket
from dataclasses import replace
from pathlib import Path

import pytest

from fixtures.pods import report
from opencs import policy
from opencs.app import main, run_city, serve
from opencs.clock import ManualClock
from opencs.config import AppConfig, load_config
from opencs.messages import ServerMessage, client, client_payload, decode_server, server
from opencs.routing import Router
from opencs.wire.frame import Frame, FrameReader, build_frame
from opencs.world.city import City, JoinResult

SHIPPED = Path(__file__).parents[2] / "config" / "server.toml"
TIMEOUT = 5.0


def local_config() -> AppConfig:
    config = load_config(SHIPPED)
    return replace(config, server=replace(config.server, host="127.0.0.1", port=0))


class Client:
    """A scripted client on a real loopback socket. Reads wait on the socket, never on time."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.buffer = b""
        self._frames = FrameReader()
        self._parsed: list[Frame] = []

    async def send(self, data: bytes | str) -> None:
        self.writer.write(data.encode("latin-1") if isinstance(data, str) else data)
        await asyncio.wait_for(self.writer.drain(), TIMEOUT)

    async def _receive(self) -> None:
        """Read once; keep the bytes for ``expect`` and any frames in them for ``expect_frame``."""
        data = await asyncio.wait_for(self.reader.read(4096), TIMEOUT)
        assert data, f"server closed; had {self.buffer!r}"
        self.buffer += data
        self._parsed += self._frames.feed(data)

    async def expect_frame(self, letter: str) -> ServerMessage:
        """Read until a server frame of this type letter has arrived; return it decoded."""
        want = letter.encode()
        while True:
            for i, frame in enumerate(self._parsed):
                if frame.payload[:1] == want:
                    del self._parsed[: i + 1]
                    return decode_server(frame.payload)[0]
            await self._receive()

    async def expect_after_clock_advances(
        self, needle: bytes, clock: ManualClock, step: float
    ) -> bytes:
        """``expect``, for something the server does once its clock has moved on. The server reads
        our bytes at some moment we cannot see, so move the clock a step at a time between reads
        until the needle comes; a read that waits on the socket for a moment is not a sleep."""
        for _ in range(50):
            if needle in self.buffer:
                break
            clock.advance(step)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._receive(), 0.2)
        return await self.expect(needle)

    async def expect(self, needle: bytes) -> bytes:
        """Read until ``needle`` has arrived; return everything up to and including it."""
        while needle not in self.buffer:
            await self._receive()
        end = self.buffer.index(needle) + len(needle)
        got, self.buffer = self.buffer[:end], self.buffer[end:]
        return got


async def start(
    config: AppConfig, *, clock: ManualClock | None = None
) -> tuple[asyncio.Event, asyncio.Task[None], int]:
    stop = asyncio.Event()
    port_future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(serve(config, stop=stop, ready=port_future.set_result, clock=clock))
    return stop, task, await asyncio.wait_for(port_future, TIMEOUT)


async def connect(port: int) -> Client:
    reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), TIMEOUT)
    return Client(reader, writer)


def test_serve_listens_then_stops_on_event() -> None:
    async def scenario() -> None:
        stop, task, port = await start(local_config())
        assert port > 0
        client = await connect(port)
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)
        # Shutdown closed the connection.
        assert await asyncio.wait_for(client.reader.read(), TIMEOUT) == b""
        client.writer.close()

    asyncio.run(scenario())


def test_a_client_logs_in_enters_the_chamber_chooses_a_city_and_launches() -> None:
    async def scenario() -> None:
        stop, task, port = await start(local_config())
        c = await connect(port)
        await c.send(b"ATZ\r\nHHH")
        await c.expect(b"CONNECT\r\nU#=")
        await c.send("\rJOINGENIE\r")
        await c.expect(b"PASSWORD: ")
        await c.send("<Not Telling!>\r")
        await c.expect(b"\r\n*")
        await c.send("M 1380;3\r")
        await c.expect(b"\x05PRESS RETURN\r")
        await c.send("CSTTBT 105 150\r")
        await c.expect(b"\x05A\r")
        await c.expect(b"WELCOME TO THE CHAMBER, JOINGENI. TYPE /HELP FOR COMMANDS.\r\x05D0\r")
        await c.send("/CITY\r")
        listing = await c.expect(b"CYBERCITY THREE")
        assert b"\x05" not in listing  # plain text only (I §5.3)
        assert b"CYBERCITY ONE" in listing and b"CYBERCITY TWO" in listing
        await c.send("/PLAY\r")
        await c.expect(b"\x05E 2 2\r")
        await c.send("/GO 0\r")
        await c.expect(b"\x05C\r")
        # In game now: the session answers at once.
        assert isinstance(await c.expect_frame("B"), server.Session)
        await c.send(build_frame(b"EN", terminator=b"\x0a"))
        assert await c.expect_frame("E") == server.PingReply()
        c.writer.close()
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)

    asyncio.run(scenario())


def test_a_player_drops_dies_quits_and_plays_again_on_one_connection() -> None:
    async def scenario() -> None:
        clock = ManualClock(1000.0)
        stop, task, port = await start(local_config(), clock=clock)
        c = await connect(port)
        await c.send("HHH\rJOINGENIE\r<Not Telling!>\rM 1380;3\r")
        await c.expect(b"\x05PRESS RETURN\r")
        await c.send("\r")
        await c.expect(b"\x05A\r")
        for game in range(2):
            await c.send("/PLAY B\r")
            await c.expect(b"\x05E 2 2\r")
            await c.send("/GO 0\r")
            await c.expect(b"\x05C\r")
            hello = await c.expect_frame("B")
            assert isinstance(hello, server.Session) and hello.self_id == 0, game
            # The first drop: the pod reports state 4, the server releases it (I §7.1).
            await c.send(build_frame(client_payload(report(4)), terminator=b"\x0a"))
            assert await c.expect_frame("A") == server.ReleaseDrop(0)
            await c.send(build_frame(b"EN", terminator=b"\x0a"))
            assert await c.expect_frame("E") == server.PingReply()
            await c.send(build_frame(client_payload(report(0)), terminator=b"\x0a"))
            await c.send(build_frame(client_payload(client.Died()), terminator=b"\x0a"))
            assert await c.expect_frame("J") == server.Destroy(0)
            await c.send(build_frame(client_payload(client.DropMe()), terminator=b"\x0a"))
            assert await c.expect_frame("A") == server.ReleaseDrop(0)
            # Shift-Q: 'C' answered with a tagged 'C'; QUIT twice; the buttons come back.
            await c.send(build_frame(client_payload(client.Quit()), terminator=b"\x0a"))
            assert await c.expect_frame("C") == server.QuitAck()
            await c.send(b"QUIT\rQUIT\r")
            await c.expect_after_clock_advances(b"\x05A\r", clock, policy.RETURN_SETTLE)
        c.writer.close()
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)

    asyncio.run(scenario())


def test_a_city_ticker_ticks_the_city_and_delivers_its_events() -> None:
    class Sink:
        def __init__(self) -> None:
            self.got: list[ServerMessage] = []

        def send(self, msg: ServerMessage) -> None:
            self.got.append(msg)

    class Stop(BaseException):
        pass

    city = City(load_config(SHIPPED).cities[0])
    result = city.join("PILOT", 0, 0.0)
    assert isinstance(result, JoinResult)
    city.client_message(result.player_id, report(4), 0.0)
    router, sink, clock = Router(), Sink(), ManualClock(10.0)
    router.register(city.config.letter, result.player_id, sink)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) > 3:
            raise Stop

    async def scenario() -> None:
        with contextlib.suppress(Stop):
            await run_city(city, router, clock, fake_sleep)

    asyncio.run(scenario())
    assert sleeps == [1 / policy.TICK_HZ] * 4
    assert sink.got == [server.ReleaseDrop(result.player_id)] * 3  # one per tick while dropping


def test_two_clients_chat_in_the_chamber_and_the_second_gets_a_new_callsign() -> None:
    async def scenario() -> None:
        stop, task, port = await start(local_config())
        clients = []
        for _ in range(2):
            c = await connect(port)
            await c.send("HHH\rJOINGENIE\r<Not Telling!>\rM 1380;3\r")
            await c.expect(b"\x05PRESS RETURN\r")
            await c.send("\r")
            await c.expect(b"\x05A\r")
            clients.append(c)
        first, second = clients
        await second.expect(b"WELCOME TO THE CHAMBER, JOINGEN2")
        await second.send("hello there\r")
        await first.expect(b"\x05D4\rJOINGEN2: hello there\r\x05D0\r")
        for c in clients:
            c.writer.close()
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)

    asyncio.run(scenario())


class ClosedSignal(logging.Handler):
    """Sets an event when a connection logs that it has closed (after its session was released)."""

    def __init__(self, event: asyncio.Event) -> None:
        super().__init__()
        self.event = event

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage().endswith("closed"):
            self.event.set()


def test_a_client_that_vanishes_frees_its_callsign() -> None:
    async def scenario() -> None:
        closed = asyncio.Event()
        handler = ClosedSignal(closed)
        net_log = logging.getLogger("opencs.net")
        net_log.addHandler(handler)
        net_log.setLevel(logging.INFO)
        try:
            stop, task, port = await start(local_config())
            first = await connect(port)
            await first.send("HHH\rJOINGENIE\r")
            await first.expect(b"PASSWORD: ")
            first.writer.close()
            await asyncio.wait_for(closed.wait(), TIMEOUT)  # the server has released the pilot
            c = await connect(port)
            await c.send("HHH\rJOINGENIE\r<Not Telling!>\rM 1380;3\r")
            await c.expect(b"\x05PRESS RETURN\r")
            await c.send("\r")
            await c.expect(b"WELCOME TO THE CHAMBER, JOINGENI.")
            c.writer.close()
            stop.set()
            await asyncio.wait_for(task, TIMEOUT)
        finally:
            net_log.removeHandler(handler)

    asyncio.run(scenario())


def test_main_reports_bad_config(tmp_path: Path) -> None:
    assert main(["--config", str(tmp_path / "missing.toml")]) == 2


def test_main_rejects_an_out_of_range_port() -> None:
    with pytest.raises(SystemExit) as info:
        main(["--config", str(SHIPPED), "--port", "70000"])
    assert info.value.code == 2


def test_main_reports_a_port_in_use(capsys: pytest.CaptureFixture[str]) -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        code = main(["--config", str(SHIPPED), "--host", "127.0.0.1", "--port", str(port)])
    err = capsys.readouterr().err
    assert code == 1
    assert f"cannot listen on 127.0.0.1:{port}" in err
    assert "Traceback" not in err
