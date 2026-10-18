import tomllib
from pathlib import Path

import pytest

import opencs
from opencs.app import main

ROOT = Path(__file__).resolve().parents[2]


def test_version_is_the_release_candidate() -> None:
    assert opencs.__version__ == "1.0.0rc1"


def test_package_and_project_metadata_agree() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["version"] == opencs.__version__
    assert project["name"] == "opencs"


def test_version_flag_prints_the_version_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["--version"])
    assert stopped.value.code == 0
    assert capsys.readouterr().out.strip() == f"opencs {opencs.__version__}"
