import asyncio
import contextlib
import logging
import socket
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from fixtures.loopback import TIMEOUT, LogWatch, TickGate, connect, local_config, start
from fixtures.pods import report
from opencs import policy
from opencs.app import Seams, StartupError, ThreadHasher, build_services, main, serve
from opencs.cities import run_city
from opencs.clock import ManualClock
from opencs.config import AdminConfig, load_config
from opencs.messages import ServerMessage, client, client_payload, server
from opencs.routing import Router
from opencs.store.base import ProgressRecord
from opencs.store.memory import memory_stores
from opencs.wire.frame import build_frame
from opencs.world.city import City
from opencs.world.seating import JoinResult

SHIPPED = Path(__file__).parents[2] / "config" / "server.toml"


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


def test_shutdown_saves_a_player_still_seated_with_an_unflushed_change() -> None:
    async def scenario() -> None:
        config = local_config()
        stores = memory_stores()
        services = build_services(config, stores=stores, clock=ManualClock())
        city = services.cities["B"]
        joined = city.join("ALICE", 0, 0.0, identity="acct:ALICE", saved=None)
        assert isinstance(joined, JoinResult)
        city.client_message(joined.player_id, report(4), 5.0)
        city.client_message(joined.player_id, client.Status(777, 1, 88), 20.0)  # never flushed
        assert stores.progress.load("acct:ALICE") is None
        stop, task, _ = await start(config, services=services)
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)
        record = stores.progress.load("acct:ALICE")
        assert isinstance(record, ProgressRecord) and (record.score, record.bay_points) == (777, 88)

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
        await c.expect(b"WELCOME TO THE CHAMBER, JOINGENIE. TYPE /HELP FOR COMMANDS.\r\x05D0\r")
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
        clock, gate = ManualClock(1000.0), TickGate()
        config = local_config()
        services = build_services(config, clock=clock)
        stop, task, port = await start(
            config, clock=clock, services=services, tick_sleep=gate.sleep
        )
        c = await connect(port)
        await c.send("HHH\rJOINGENIE\r<Not Telling!>\rM 1380;3\r")
        await c.expect(b"\x05PRESS RETURN\r")
        await c.send("\r")
        await c.expect(b"\x05A\r")
        with LogWatch("opencs.session", "QUIT line") as quit_lines:
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
                await quit_lines.reached(2 * (game + 1))  # the server has read both
                assert services.cities["B"].players() == []
                clock.advance(policy.RETURN_SETTLE)
                gate.pulse()  # one tick of the session: the settle time is up
                await c.expect(b"\x05A\r")
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
        await second.expect(b"WELCOME TO THE CHAMBER, JOINGENIE2")
        await second.send("hello there\r")
        await first.expect(b"\x05D4\rJOINGENIE2: hello there\r\x05D0\r")
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
            await c.expect(b"WELCOME TO THE CHAMBER, JOINGENIE.")
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


def test_main_names_an_unusable_data_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    not_a_directory = tmp_path / "file"
    not_a_directory.write_text("x")
    code = main(["--config", str(SHIPPED), "--port", "0", "--data-dir", str(not_a_directory)])
    err = capsys.readouterr().err
    assert code == 1
    assert "cannot open the database" in err and str(not_a_directory) in err
    assert "cannot listen" not in err and "Traceback" not in err


def test_an_admin_port_in_use_is_named_as_the_admin_port() -> None:
    async def scenario() -> None:
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            port = taken.getsockname()[1]
            base = local_config()
            config = replace(
                base, server=replace(base.server, admin=AdminConfig(port=port, token="t" * 20))
            )
            with pytest.raises(StartupError, match=f"admin channel on 127.0.0.1:{port}"):
                await serve(config, stop=asyncio.Event(), seams=Seams(stores=memory_stores()))

    asyncio.run(asyncio.wait_for(scenario(), TIMEOUT))


def test_password_hashes_run_on_a_thread_and_report_back_on_the_loop() -> None:
    async def scenario() -> tuple[int, int, int, bool]:
        hasher = ThreadHasher(workers=1)
        release = threading.Event()
        worked_on: list[int] = []
        done_on: list[int] = []
        finished = asyncio.Event()

        def work() -> bytes:
            worked_on.append(threading.get_ident())
            release.wait(5)
            return b"digest"

        def done(digest: bytes) -> None:
            assert digest == b"digest"
            done_on.append(threading.get_ident())
            finished.set()

        hasher.submit(work, done)
        await asyncio.sleep(0.01)  # the loop is free while the hash runs
        loop_ran_meanwhile = not finished.is_set()
        release.set()
        await asyncio.wait_for(finished.wait(), 5)
        hasher.close()
        return worked_on[0], done_on[0], threading.get_ident(), loop_ran_meanwhile

    worker, reported, loop_thread, loop_ran = asyncio.run(scenario())
    assert worker != loop_thread
    assert reported == loop_thread
    assert loop_ran
