"""docs/OPERATING.md names only options, keys and limits the server has."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from opencs import policy
from opencs.app import _parse_args
from opencs.config import load_config

ROOT = Path(__file__).resolve().parents[2]
GUIDE = (ROOT / "docs" / "OPERATING.md").read_text()


def fenced(text: str, language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)```", text, re.DOTALL)


def test_every_policy_constant_the_guides_name_exists() -> None:
    names = set(re.findall(r"`([A-Z][A-Z_]{4,})`", GUIDE))
    constants = {n for n in names if "_" in n and not n.startswith("OPENCS_")}
    assert {"MAX_CONNECTIONS", "MAX_CONNECTIONS_PER_ADDRESS"} <= constants
    assert [n for n in sorted(constants) if not hasattr(policy, n)] == []


def test_the_command_line_options_the_unit_uses_are_accepted() -> None:
    for unit in fenced(GUIDE, "ini"):
        for line in unit.splitlines():
            if line.startswith("ExecStart="):
                args = re.sub(r"%i", "one", line.split("-m opencs", 1)[1]).split()
                parsed = _parse_args(args)
                paths = dict(zip(args[::2], args[1::2], strict=False))
                assert parsed.config == Path(paths["--config"])
                assert parsed.data_dir == Path(paths["--data-dir"])


def test_every_environment_file_in_a_unit_is_optional() -> None:
    """systemd refuses a unit whose EnvironmentFile is missing unless the path starts with ``-``;
    the token file exists only when the admin channel is used."""
    lines = [
        line
        for unit in fenced(GUIDE, "ini")
        for line in unit.splitlines()
        if line.startswith("EnvironmentFile=")
    ]
    assert len(lines) >= 2
    assert [x for x in lines if not x.startswith("EnvironmentFile=-")] == []


def test_the_server_toml_in_the_guide_loads(tmp_path: Path) -> None:
    snippet = next(t for t in fenced(GUIDE, "toml") if "[admin]" in t)
    shutil.copytree(ROOT / "config" / "cities", tmp_path / "cities")
    (tmp_path / "server.toml").write_text(
        snippet.replace('"/var/lib/opencs"', f'"{tmp_path / "data"}"').replace(
            '"/var/log/opencs/opencs.log"', f'"{tmp_path / "opencs.log"}"'
        )
    )
    config = load_config(tmp_path / "server.toml").server
    assert config.port == 2399
    assert config.admin.port == 2400
    assert config.logging.file == tmp_path / "opencs.log"


def test_the_guide_uses_the_default_game_port() -> None:
    default = load_config(ROOT / "config" / "server.toml").server
    assert default.port == 2399
    assert "TCP 2399" in GUIDE


def test_systemd_units_keep_comments_on_their_own_lines() -> None:
    """systemd takes a trailing ``# ...`` as part of the value."""
    for unit in fenced(GUIDE, "ini"):
        for line in unit.splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                assert " #" not in line, line
