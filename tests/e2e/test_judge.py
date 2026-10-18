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
    assert re.search(
        r"^ok +2 /PLAY answered with a city selection \('E'\) and the launch \('C'\)$",
        result.stdout,
        re.MULTILINE,
    )
    assert "FAIL" not in result.stdout


SMOKE = ROOT / "tools" / "smoke.sh"


def test_smoke_script_is_executable_and_runs_all_nineteen_checks() -> None:
    """Static: the operator's one command must select every check and refuse a short pass."""
    text = SMOKE.read_text()
    assert SMOKE.stat().st_mode & 0o111
    assert "tools/judge.py" in text
    assert "--checks 1-19" in text
    assert "-ne 19" in text


@pytest.mark.judge
def test_smoke_script_passes_against_a_fresh_server() -> None:
    result = subprocess.run(
        [str(SMOKE)],
        check=False,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(re.findall(r"^ok +\d+ ", result.stdout, re.MULTILINE)) == 19
    assert "smoke: ok (19 of 19 checks passed)" in result.stdout
