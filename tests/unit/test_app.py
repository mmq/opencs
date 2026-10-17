import asyncio
import logging
import socket
from dataclasses import replace
from pathlib import Path

import pytest

from opencs.app import main, serve
from opencs.config import AppConfig, load_config
from opencs.wire.frame import build_frame

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

    async def send(self, data: bytes | str) -> None:
        self.writer.write(data.encode("latin-1") if isinstance(data, str) else data)
        await asyncio.wait_for(self.writer.drain(), TIMEOUT)

    async def expect(self, needle: bytes) -> bytes:
        """Read until ``needle`` has arrived; return everything up to and including it."""
        while needle not in self.buffer:
            data = await asyncio.wait_for(self.reader.read(4096), TIMEOUT)
            assert data, f"server closed while waiting for {needle!r}; had {self.buffer!r}"
            self.buffer += data
        end = self.buffer.index(needle) + len(needle)
        got, self.buffer = self.buffer[:end], self.buffer[end:]
        return got


async def start(config: AppConfig) -> tuple[asyncio.Event, asyncio.Task[None], int]:
    stop = asyncio.Event()
    port_future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(serve(config, stop=stop, ready=port_future.set_result))
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
        # In game now: binary frames are accepted (and, until M4, ignored).
        await c.send(build_frame(b"EN", terminator=b"\x0a"))
        c.writer.close()
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)

    asyncio.run(scenario())


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
