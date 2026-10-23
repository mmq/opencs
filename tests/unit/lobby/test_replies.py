"""The Chamber's replies: content, arming and color bytes (II §5 and "Colors")."""

from __future__ import annotations

import pytest

from fixtures.lobbies import add, city, fill, make
from opencs.lobby import replies
from opencs.store.base import ProgressRecord
from opencs.wire.text import ARMED_LINE_LIMIT

RECORD = ProgressRecord((3, 2, 1, 1, 5, 4, 0, 1, 2), -2, 957, 1670, 90_000)


def text(lines: list[replies.Line]) -> str:
    return replies.render(lines).decode("ascii")


def test_render_colors_each_block_and_ends_with_d0() -> None:
    data = replies.render([replies.heading("H"), replies.body("a"), replies.body("b")])
    assert data == b"\x05D6\rH\r\x05D2\ra\rb\r\x05D0\r"


def test_render_error_block() -> None:
    assert replies.render(replies.denied("NOPE")) == b"\x05D3\rNOPE\r\x05D0\r"


def test_uncolored_lines_have_no_arming_bytes() -> None:
    data = replies.render([replies.plain("x"), replies.plain("y")])
    assert data == b"x\ry\r"


def test_every_reply_is_printable_ascii_with_cr_and_short_armed_lines() -> None:
    lobby = make()
    add(lobby, "ALPHA", "B", 1)
    add(lobby, "BRAVO")
    me = lobby.find("BRAVO")
    assert me is not None
    everything = [
        replies.welcome("BRAVO"),
        replies.help_lines(),
        replies.city_list(lobby, "draft"),
        replies.city_hint("B"),
        replies.city_info(lobby, lobby.cities()[1]),
        replies.status(lobby),
        replies.who_is(me, "BRAVO", RECORD),
        replies.who_is(me, "BRAVO", None),
        replies.who_is(None, "NOBODY", None),
        replies.score("BRAVO", RECORD),
        replies.score("BRAVO", None),
        replies.resume_refused(guest=True),
        replies.resume_refused(guest=False),
        replies.colors(),
        replies.unknown("X"),
        replies.usage("USAGE"),
    ]
    for lines in everything:
        data = replies.render(lines)
        for raw in data.split(b"\r")[:-1]:
            body = raw.removeprefix(b"\x05")
            assert all(0x20 <= b <= 0x7E for b in body), raw
            assert len(raw) + 1 < ARMED_LINE_LIMIT
        assert data.endswith(b"\r")


def test_bare_city_list_is_plain_text_with_no_arming_bytes() -> None:
    lobby = make()
    fill(lobby, "B", 5)
    data = replies.render(replies.city_list(lobby))
    assert b"\x01" not in data
    assert b"\x05" not in data
    rows = data.decode().split("\r")[:-1]
    assert len(rows) == 3
    assert rows[0].startswith("A  CITY A")
    assert rows[1].split() == ["B", "CITY", "B", "5/16"]
    assert rows[2].endswith("0/16")


def test_bare_city_list_lists_only_enabled_cities_by_letter() -> None:
    lobby = make(city("A"), city("C", enabled=False), city("D"))
    rows = replies.render(replies.city_list(lobby)).decode().split("\r")[:-1]
    assert [r[0] for r in rows] == ["A", "D"]


def test_city_list_with_a_draft_starts_with_not_sent() -> None:
    lobby = make()
    rows = replies.render(replies.city_list(lobby, "hello wor")).decode().split("\r")
    assert rows[0] == "NOT SENT: hello wor"
    assert b"\x05" not in replies.render(replies.city_list(lobby, "x"))


def test_city_info_lists_teams_counts_and_callsigns() -> None:
    lobby = make()
    add(lobby, "ZED", "B", 1)
    add(lobby, "ABE", "B", 1)
    add(lobby, "OUT", "A", 1)
    out = text(replies.city_info(lobby, lobby.cities()[1]))
    assert "CITY B: CITY B  3/16" not in out  # 3 players are not all in B
    assert "CITY B: CITY B  2/16" in out
    assert "RED   0/4  -" in out
    assert "BLUE  2/4  ABE ZED" in out
    assert "GREEN 0/4  -" in out
    assert "BLACK 0/4  -" in out
    assert "OUT" not in out


def test_status_lists_every_pilot_and_where() -> None:
    lobby = make()
    add(lobby, "BRAVO")
    add(lobby, "ALPHA", "C", 3)
    add(lobby, "CHARLIE", "A", 0)
    out = text(replies.status(lobby))
    assert "PILOTS ONLINE: 3" in out
    rows = out.split("\r")
    assert rows.index("ALPHA         C BLACK") < rows.index("BRAVO         CHAMBER")
    assert "CHARLIE       A RED" in rows


def test_who_is_gives_location_and_the_record_or_an_error() -> None:
    lobby = make()
    add(lobby, "ALPHA", "B", 2)
    found = lobby.find("ALPHA")
    out = text(replies.who_is(found, "ALPHA", RECORD))
    assert "ALPHA: B GREEN" in out
    assert "LAST GAME SCORE: 1670" in out and "BATTLE MARKER: -2" in out
    assert "NO RECORD YET" in text(replies.who_is(found, "ALPHA", None))
    missing = replies.render(replies.who_is(None, "GHOST", RECORD))
    assert b"NO SUCH PILOT: GHOST" in missing and b"1670" not in missing
    assert missing.startswith(b"\x05D3\r")


def test_score_shows_every_field_of_the_record_in_the_manuals_terms() -> None:
    rows = [r for r in text(replies.score("BRAVO", RECORD)).split("\r") if not r.startswith("\x05")]
    assert rows[:6] == [
        "RECORD OF BRAVO",
        "LAST GAME SCORE: 1670",
        "CAREER TOTAL: 90000",
        "BAY POINTS: 957",
        "BAYS: 3 2 1 1 5 4 0 1 2",
        "BATTLE MARKER: -2",
    ]
    assert "NO RECORD YET" in text(replies.score("BRAVO", None))


def test_resume_replies_distinguish_guests() -> None:
    assert "JOINGENIE" in text(replies.resume_refused(guest=True))
    assert "NO GAME" in text(replies.resume_refused(guest=False))


def test_colors_shows_the_seven_usable_digits_each_in_its_color() -> None:
    data = replies.render(replies.colors())
    for n in range(7):
        assert f"\x05D{n}\rD{n}  THIS IS COLOR {n}\r".encode() in data
    assert b"D7" not in data
    assert data.endswith(b"\x05D0\r")


def test_help_names_every_command() -> None:
    out = text(replies.help_lines())
    for word in ("/CITY", "/PLAY", "/STATUS", "/WHO", "/SCORE", "/RESUME", "/COLORS", "/QUIT"):
        assert word in out


@pytest.mark.parametrize("word", ["", "X"])
def test_unknown_names_the_word(word: str) -> None:
    assert f"/{word}." in text(replies.unknown(word))
