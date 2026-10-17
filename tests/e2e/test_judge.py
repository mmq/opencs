"""The black-box judge against a server it starts (real time: tens of seconds, so not in the
default run; ``uv run pytest -m judge``)."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
JUDGE = ROOT / "tools" / "judge.py"
TIMEOUT = 180


@pytest.mark.judge
def test_judge_checks_1_and_2_pass_login_and_city_selection() -> None:
    result = subprocess.run(
        [sys.executable, str(JUDGE), "--server", "--checks", "1,2"],
        check=False,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"^ok +1 login and Chamber reached$", result.stdout, re.MULTILINE)
    assert re.search(r"^ok +2 /PLAY answered with a city selection", result.stdout, re.MULTILINE)
    assert "FAIL" not in result.stdout
