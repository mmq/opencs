import asyncio
from dataclasses import replace
from pathlib import Path

from opencs.app import main, serve
from opencs.config import AppConfig, load_config

SHIPPED = Path(__file__).parents[2] / "config" / "server.toml"
TIMEOUT = 5.0


def local_config() -> AppConfig:
    config = load_config(SHIPPED)
    return replace(config, server=replace(config.server, host="127.0.0.1", port=0))


def test_serve_accepts_a_connection_and_stops_on_event() -> None:
    async def scenario() -> None:
        stop = asyncio.Event()
        port_future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(serve(local_config(), stop=stop, ready=port_future.set_result))
        port = await asyncio.wait_for(port_future, TIMEOUT)
        assert port > 0
        reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), TIMEOUT)
        # The M0 listener closes every connection at once.
        assert await asyncio.wait_for(reader.read(), TIMEOUT) == b""
        writer.close()
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)

    asyncio.run(scenario())


def test_main_reports_bad_config(tmp_path: Path) -> None:
    assert main(["--config", str(tmp_path / "missing.toml")]) == 2
