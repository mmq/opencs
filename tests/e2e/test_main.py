import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
CONFIG = ROOT / "config" / "server.toml"
TIMEOUT = 15.0


@pytest.mark.e2e
def test_main_listens_then_exits_zero_on_sigterm() -> None:
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "opencs",
            "--port",
            "0",
            "--config",
            str(ROOT / "config/server.toml"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdout is not None
    # A watchdog turns a hung child into EOF on the pipe instead of a hung test.
    watchdog = threading.Timer(TIMEOUT, proc.kill)
    watchdog.start()
    try:
        lines: list[str] = []
        for line in proc.stdout:
            lines.append(line)
            if "listening on" in line:
                break
        else:
            pytest.fail("no 'listening on' line:\n" + "".join(lines))
        proc.send_signal(signal.SIGTERM)
        lines.extend(proc.stdout)
        assert proc.wait(TIMEOUT) == 0
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            proc.kill()
    assert "Traceback" not in "".join(lines)
