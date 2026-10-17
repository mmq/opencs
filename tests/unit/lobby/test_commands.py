"""Parsing Chamber lines (I §5.3, II §4), including the traps the buttons set."""

from __future__ import annotations

import pytest

from opencs.lobby.commands import (
    BareLetter,
    CityInfo,
    CityList,
    Colors,
    Go,
    Help,
    Nothing,
    Play,
    Quit,
    Resume,
    Say,
    Score,
    Status,
    Unknown,
    Usage,
    WhoIs,
    parse,
    team_from_word,
)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("", Nothing()),
        ("   ", Nothing()),
        ("/HELP", Help()),
        ("/help", Help()),
        ("  /Help  ", Help()),
        ("/STATUS", Status()),
        ("/WHO", Status()),
        ("/who alpha", WhoIs("ALPHA")),
        ("/SCORE", Score()),
        ("/RESUME", Resume()),
        ("/COLORS", Colors()),
        ("/QUIT", Quit()),
        ("/quit now", Quit()),
        ("/CITY b", CityInfo("B")),
        ("/city B", CityInfo("B")),
        ("/GO 0", Go(0)),
        ("/go 3", Go(3)),
        ("hello there", Say("hello there")),
        ("a b", Say("a b")),
    ],
)
def test_simple_commands(line: str, expected: object) -> None:
    assert parse(line) == expected


@pytest.mark.parametrize("line", ["/CITY", "/city", "  /CITY  ", "/Wrong/CITY"])
def test_a_line_ending_in_city_is_a_bare_city(line: str) -> None:
    assert isinstance(parse(line), CityList)


def test_a_draft_before_city_is_kept_but_not_relayed() -> None:
    assert parse("/CITY") == CityList("")
    assert parse("draft/CITY") == CityList("draft")
    assert parse("hello world/city") == CityList("hello world")
    assert parse("/PLAY /CITY") == CityList("/PLAY")
    assert parse("/WHO /CITY") == CityList("/WHO")


def test_city_in_the_middle_is_not_a_bare_city() -> None:
    assert parse("/CITYS") == Unknown("CITYS")
    assert parse("the /CITY button") == Say("the /CITY button")
    assert parse("/CITY B") == CityInfo("B")


def test_a_single_letter_is_a_bare_letter_otherwise_chat() -> None:
    assert parse("b") == BareLetter("B", "b")
    assert parse("C") == BareLetter("C", "C")
    assert parse("1") == Say("1")
    assert parse("bb") == Say("bb")
    assert parse("!") == Say("!")


def test_play_forms() -> None:
    assert parse("/PLAY") == Play()
    assert parse("/play b") == Play("B", None)
    assert parse("/PLAY B GREEN") == Play("B", 2)
    assert parse("/PLAY c black") == Play("C", 3)
    assert parse("/PLAY A 0") == Play("A", 0)


@pytest.mark.parametrize(
    "line", ["/PLAY 1", "/PLAY BB", "/PLAY B PURPLE", "/PLAY B RED X", "/PLAY B 4", "/PLAY !"]
)
def test_bad_play_arguments_get_usage(line: str) -> None:
    assert isinstance(parse(line), Usage)


@pytest.mark.parametrize("line", ["/CITY 1", "/CITY BC", "/CITY B C"])
def test_bad_city_arguments_get_usage(line: str) -> None:
    assert isinstance(parse(line), Usage)


def test_unknown_commands() -> None:
    assert parse("/dance") == Unknown("DANCE")
    assert parse("/") == Unknown("")
    assert parse("/GO") == Unknown("GO")
    assert parse("/GO x") == Unknown("GO")
    assert parse("/" + "x" * 40) == Unknown("X" * 16)


@pytest.mark.parametrize(
    ("word", "team"),
    [
        ("RED", 0),
        ("blue", 1),
        ("Green", 2),
        ("BLACK", 3),
        ("R", 0),
        ("B", 1),  # BLUE, not BLACK
        ("G", 2),
        ("0", 0),
        ("1", 1),
        ("2", 2),
        ("3", 3),
        ("4", None),
        ("K", None),
        ("", None),
        ("PURPLE", None),
        ("10", None),
        ("-1", None),
    ],
)
def test_team_words(word: str, team: int | None) -> None:
    assert team_from_word(word) == team
