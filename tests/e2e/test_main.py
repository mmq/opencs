import re
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from opencs import policy

ROOT = Path(__file__).parents[2]
CONFIG = ROOT / "config" / "server.toml"
TIMEOUT = 15.0
LISTENING = re.compile(r"listening on (\S+):(\d+)")


@pytest.mark.e2e
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_main_listens_on_the_given_address_then_exits_zero(
    sig: signal.Signals, tmp_path: Path
) -> None:
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "opencs",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--config",
            str(CONFIG),
            "--data-dir",
            str(tmp_path),
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
            found = LISTENING.search(line)
            if found:
                break
        else:
            pytest.fail("no 'listening on' line:\n" + "".join(lines))
        # The overrides took effect: the configured port is 2399, the flag asked for any free one.
        assert found.group(1) == "127.0.0.1"
        port = int(found.group(2))
        assert port not in (0, policy.DEFAULT_PORT)
        socket.create_connection(("127.0.0.1", port), timeout=TIMEOUT).close()
        proc.send_signal(sig)
        lines.extend(proc.stdout)
        assert proc.wait(TIMEOUT) == 0
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            proc.kill()
    assert "Traceback" not in "".join(lines)
    assert (tmp_path / policy.DATABASE_FILE_NAME).exists()  # the flag took effect, not ../data
