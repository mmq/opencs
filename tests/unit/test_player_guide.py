"""The player guide names only commands the Chamber understands (docs/PLAYING.md)."""

from __future__ import annotations

import re
from pathlib import Path

from opencs import policy
from opencs.config import load_config
from opencs.lobby import commands
from opencs.lobby.replies import HELP_LINES

ROOT = Path(__file__).resolve().parents[2]
GUIDE = (ROOT / "docs" / "PLAYING.md").read_text()
#: A backticked command: `/PLAY <letter> [team]`.
COMMAND = re.compile(r"`(/[A-Za-z]+[^`]*)`")


def concrete(example: str) -> str:
    """The guide's placeholders replaced by values a player would type."""
    text = re.sub(r"\[[^\]]*\]", "", example)
    text = text.replace("<letter>", "B").replace("<callsign>", "ALPHA")
    return " ".join(text.split())


def test_every_command_in_the_guide_parses_to_a_known_command() -> None:
    # `/GO` is the game's own reply while launching; the guide names it only to say it is not typed.
    found = sorted({concrete(m) for m in COMMAND.findall(GUIDE)} - {"/GO"})
    assert {"/HELP", "/CITY", "/CITY B", "/PLAY", "/PLAY B", "/WHO ALPHA", "/RESUME"} <= set(found)
    unknown = [c for c in found if isinstance(commands.parse(c), commands.Unknown | commands.Usage)]
    assert unknown == []


def test_the_guide_covers_every_command_the_lobby_documents() -> None:
    listed = {line.split()[0] for line in HELP_LINES if line.startswith("/")}
    missing = [c for c in sorted(listed) if f"`{c}" not in GUIDE]
    assert missing == []


def test_the_guide_gives_the_resume_windows_the_server_uses() -> None:
    minutes = {int(policy.HOLD_SECONDS // 60), int(policy.TYPE_SECONDS // 60)}
    assert minutes == {10, 3}
    flat = " ".join(GUIDE.split())
    assert "within **10 minutes** of the drop" in flat
    assert "**3 minutes after re-entering the Chamber**" in flat


def test_the_guide_city_table_matches_the_shipped_city_files() -> None:
    cities = load_config(ROOT / "config" / "server.toml").cities
    assert [c.letter for c in cities] == ["A", "B", "C"]
    rows = {
        cells[0]: cells
        for line in GUIDE.splitlines()
        if line.startswith("| ")
        for cells in [[c.strip() for c in line.strip("| \n").split("|")]]
    }
    output = {0: "Low", 1: "Normal"}
    for city in cities:
        _, name, map_no, ceiling, towers, level, hazards = rows[city.letter]
        assert name == city.name
        assert int(map_no) == city.map
        assert int(ceiling) == city.max_multiplier
        assert towers == f"{output[city.tower_output]} output"
        assert level == f"up to level {city.max_module_level}"
        if city.sentinels:
            assert f"{city.sentinels} Sentinels" in hazards
        else:
            assert "Sentinels" not in hazards
        if city.muncher_every:
            assert f"a free Muncher every {int(city.muncher_every // 60)} minutes" in hazards
        else:
            assert "Muncher" not in hazards
