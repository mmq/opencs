"""Parsing a Chamber line into a typed command (I §5.3, II §4.2). Pure: no state, no time.

The client's buttons type onto an unfinished input line, so parsing is defensive: a line *ending*
in ``/CITY`` is a bare ``/CITY`` whatever precedes it (II §4.2), commands are case-insensitive,
and ``/HELP`` and ``/RESUME`` (in no client binary) are ours to understand as free text (I §5.3).
Whether a bare letter is a hint or chat depends on time, so it is returned as ``BareLetter`` for
the session to decide.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from opencs import policy

CITY_SUFFIX = "/CITY"


@dataclass(frozen=True, slots=True)
class Nothing:
    """An empty line: ignored."""


@dataclass(frozen=True, slots=True)
class Say:
    """A plain line, to be relayed to the Chamber (II §4.3)."""

    text: str


@dataclass(frozen=True, slots=True)
class Help:
    """``/HELP``."""


@dataclass(frozen=True, slots=True)
class CityList:
    """A bare ``/CITY``. ``draft`` is what preceded it on the input line (``draft/CITY``), which
    is not relayed (II §4.3)."""

    draft: str = ""


@dataclass(frozen=True, slots=True)
class CityInfo:
    """``/CITY <letter>``."""

    letter: str


@dataclass(frozen=True, slots=True)
class Status:
    """``/STATUS`` or a bare ``/WHO``: every connected pilot and where."""


@dataclass(frozen=True, slots=True)
class WhoIs:
    """``/WHO <callsign>``."""

    callsign: str


@dataclass(frozen=True, slots=True)
class Score:
    """``/SCORE``."""


@dataclass(frozen=True, slots=True)
class Play:
    """``/PLAY`` (``letter`` None) or ``/PLAY <letter> [team]``; ``team`` is 0-3 or None."""

    letter: str | None = None
    team: int | None = None


@dataclass(frozen=True, slots=True)
class Resume:
    """``/RESUME``."""


@dataclass(frozen=True, slots=True)
class Colors:
    """``/COLORS``."""


@dataclass(frozen=True, slots=True)
class Quit:
    """``/QUIT``: leave the Chamber for the GEnie prompt."""


@dataclass(frozen=True, slots=True)
class Go:
    """``/GO <status>``: the client's answer to ``E <map> <map>`` (I §5.4)."""

    status: int


@dataclass(frozen=True, slots=True)
class BareLetter:
    """A single letter on its own line: a hint after a bare ``/CITY``, otherwise chat."""

    letter: str
    text: str


@dataclass(frozen=True, slots=True)
class Unknown:
    """A ``/word`` that is not a command."""

    word: str


@dataclass(frozen=True, slots=True)
class Usage:
    """A known command with arguments that do not fit; ``text`` is the usage line to show."""

    text: str


type Command = (
    Nothing
    | Say
    | Help
    | CityList
    | CityInfo
    | Status
    | WhoIs
    | Score
    | Play
    | Resume
    | Colors
    | Quit
    | Go
    | BareLetter
    | Unknown
    | Usage
)

_USAGE_CITY = "USAGE: /CITY [LETTER]"
_USAGE_PLAY = "USAGE: /PLAY [LETTER [RED|BLUE|GREEN|BLACK]]"

#: Longest unknown command word echoed back; keeps the reply line short.
_WORD_ECHO_LIMIT = 16


def team_from_word(word: str) -> int | None:
    """A team number for ``RED``/``BLUE``/``GREEN``/``BLACK``, ``R``/``B``/``G`` or ``0``-``3``.

    ``B`` is BLUE, so BLACK needs its name or ``3`` (II §6).
    """
    word = word.upper()
    if word in policy.TEAM_NAMES:
        return policy.TEAM_NAMES.index(word)
    if len(word) == 1:
        if word.isdigit() and int(word) < policy.TEAM_COUNT:
            return int(word)
        for number, name in enumerate(policy.TEAM_NAMES):
            if name[0] == word:  # first match wins: B is BLUE, never BLACK
                return number
    return None


def _letter(word: str) -> str | None:
    """``word`` upper-cased if it is a single city letter A-Z."""
    word = word.upper()
    return word if len(word) == 1 and "A" <= word <= "Z" else None


def parse(line: str) -> Command:
    """The command ``line`` (printable ASCII, no terminator) stands for."""
    text = line.strip()
    if not text:
        return Nothing()
    if text.upper().endswith(CITY_SUFFIX):
        return CityList(text[: -len(CITY_SUFFIX)].strip())
    if not text.startswith("/"):
        letter = _letter(text)
        return BareLetter(letter, text) if letter else Say(text)
    words = text[1:].split()
    if not words:
        return Unknown("")
    return _slash(words[0].upper(), words[1:])


_NO_ARGUMENTS: dict[str, Callable[[], Command]] = {
    "HELP": Help,
    "STATUS": Status,
    "SCORE": Score,
    "RESUME": Resume,
    "COLORS": Colors,
    "QUIT": Quit,
}


def _slash(word: str, args: list[str]) -> Command:
    if word in _NO_ARGUMENTS:
        return _NO_ARGUMENTS[word]()
    if word == "CITY":
        letter = _letter(args[0]) if len(args) == 1 else None
        return CityInfo(letter) if letter else Usage(_USAGE_CITY)
    if word == "WHO":
        return WhoIs(args[0].upper()) if args else Status()
    if word == "PLAY":
        return _play(args)
    if word == "GO" and len(args) == 1 and args[0].isdecimal():
        return Go(int(args[0]))
    return Unknown(word[:_WORD_ECHO_LIMIT])


def _play(args: list[str]) -> Command:
    if not args:
        return Play()
    letter = _letter(args[0])
    if letter is None or len(args) > 2:
        return Usage(_USAGE_PLAY)
    if len(args) == 1:
        return Play(letter)
    team = team_from_word(args[1])
    return Play(letter, team) if team is not None else Usage(_USAGE_PLAY)
