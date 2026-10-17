import asyncio
import socket
from dataclasses import replace
from pathlib import Path

import pytest

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
