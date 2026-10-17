"""The text the Chamber shows in answer to lobby commands (II §5, "Colors").

Replies are built as ``Line`` values and rendered by ``render``: colored lines are preceded by a
``0x05 D<n>`` color command and each colored block ends with ``D0``. The bare ``/CITY`` reply is
all uncolored, plain text: an armed line in it loses the city buttons' second half (I §5.3,
II §4.2). Everything is printable ASCII with CR line ends (I §5.2).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from opencs import policy
from opencs.wire.text import arm_line, plain_line

if TYPE_CHECKING:
    from opencs.config import CityConfig
    from opencs.lobby.lobby import Lobby, Member
    from opencs.store.base import ProgressRecord


@dataclass(frozen=True, slots=True)
class Line:
    """One displayed line; ``color`` is a ``D`` digit, or None for the current color."""

    text: str
    color: int | None = None


def plain(text: str) -> Line:
    return Line(text)


def body(text: str) -> Line:
    return Line(text, policy.COLOR_TEXT)


def heading(text: str) -> Line:
    return Line(text, policy.COLOR_HEADING)


def error(text: str) -> Line:
    return Line(text, policy.COLOR_ERROR)


def render(lines: Iterable[Line]) -> bytes:
    """The bytes for ``lines``; a color command only when the color changes, ``D0`` at the end
    of a colored block. Uncolored lines alone produce no arming byte at all."""
    out = bytearray()
    current: int | None = None
    for line in lines:
        if line.color is not None and line.color != current:
            out += arm_line(f"D{line.color}")
            current = line.color
        out += plain_line(line.text)
    if current is not None and current != policy.COLOR_RESET:
        out += arm_line(f"D{policy.COLOR_RESET}")
    return bytes(out)


def chat_line(callsign: str, text: str) -> bytes:
    """A relayed Chamber line, in the chat color (II §4.4)."""
    return render([Line(f"{callsign}: {text}", policy.COLOR_CHAT)])


def where(member: Member) -> str:
    """``CHAMBER``, or the city letter and team (II §4.2)."""
    if member.city is None or member.team is None:
        return "CHAMBER"
    return f"{member.city} {policy.TEAM_NAMES[member.team]}"


# --- replies -----------------------------------------------------------------------------------

#: Command list shown by ``/HELP``; ours.
HELP_LINES = (
    "/CITY            LIST THE CITIES",
    "/CITY <LETTER>   TEAMS AND PILOTS OF A CITY",
    "/PLAY            JOIN THE BUSIEST CITY WITH ROOM",
    "/PLAY <LETTER> [TEAM]   JOIN A CITY AND TEAM",
    "/STATUS /WHO     WHO IS ONLINE AND WHERE",
    "/WHO <CALLSIGN>  ONE PILOT",
    "/SCORE           YOUR RECORD",
    "/RESUME          RETURN TO A GAME YOU LOST",
    "/COLORS          THE TEXT COLORS",
    "/QUIT            LEAVE THE CHAMBER",
    "ANYTHING ELSE IS SENT TO PILOTS IN THE CHAMBER",
)


def welcome(callsign: str) -> list[Line]:
    return [body(f"WELCOME TO THE CHAMBER, {callsign}. TYPE /HELP FOR COMMANDS.")]


def help_lines() -> list[Line]:
    return [heading("COMMANDS"), *(body(t) for t in HELP_LINES)]


def city_list(lobby: Lobby, draft: str = "") -> list[Line]:
    """Plain text only, one line per enabled city (II §4.2)."""
    lines = [plain(f"NOT SENT: {draft}")] if draft else []
    for city in lobby.cities():
        pilots = f"{lobby.count(city.letter)}/{city.capacity}"
        lines.append(plain(f"{city.letter}  {city.name:<{policy.MAX_CITY_NAME_LENGTH}}  {pilots}"))
    if not lobby.cities():
        lines.append(plain("NO CITIES ARE OPEN"))
    return lines


def city_hint(letter: str) -> list[Line]:
    return [body(f"TYPE /CITY {letter} FOR INFORMATION OR /PLAY {letter} TO PLAY")]


def city_info(lobby: Lobby, city: CityConfig) -> list[Line]:
    lines = [
        heading(f"CITY {city.letter}: {city.name}  {lobby.count(city.letter)}/{city.capacity}")
    ]
    for team, name in enumerate(policy.TEAM_NAMES):
        members = lobby.team_members(city.letter, team)
        names = " ".join(m.callsign for m in members) or "-"
        lines.append(body(f"{name:<5} {len(members)}/{policy.MAX_PLAYERS_PER_TEAM}  {names}"))
    return lines


def status(lobby: Lobby) -> list[Line]:
    members = lobby.members()
    lines = [heading(f"PILOTS ONLINE: {len(members)}")]
    lines += (body(f"{m.callsign:<{policy.MAX_CALLSIGN_LENGTH}}  {where(m)}") for m in members)
    return lines


def record_lines(record: ProgressRecord) -> list[Line]:
    """A player's persistent record (II §4.2, II §11.3): the latest game's SCORE, the
    career total of the games before it, bay points, the nine bays and the battle marker."""
    bays = " ".join(str(level) for level in record.bays)
    return [
        body(f"LAST GAME SCORE: {record.score}"),
        body(f"CAREER TOTAL: {record.career}"),
        body(f"BAY POINTS: {record.bay_points}"),
        body(f"BAYS: {bays}"),
        body(f"BATTLE MARKER: {record.marker}"),
    ]


def who_is(member: Member | None, callsign: str, record: ProgressRecord | None) -> list[Line]:
    """One pilot: where, and the record kept for it (II §4.2)."""
    if member is None:
        return [error(f"NO SUCH PILOT: {callsign}")]
    lines = [body(f"{member.callsign}: {where(member)}")]
    return lines + (record_lines(record) if record else [body("NO RECORD YET")])


def score(callsign: str, record: ProgressRecord | None) -> list[Line]:
    """The player's own record (II §4.2)."""
    if record is None:
        return [body("NO RECORD YET: PLAY A GAME AND YOUR PROGRESS IS KEPT")]
    return [heading(f"RECORD OF {callsign}"), *record_lines(record)]


def resume_held(letter: str, seconds_left: float) -> list[Line]:
    """Back in the Chamber with a game held: where, and how long there is to type ``/RESUME``
    (II §13 item 4)."""
    left = int(seconds_left)
    return [
        heading(f"YOUR GAME IN CITY {letter} WAS INTERRUPTED."),
        heading(f"TYPE /RESUME WITHIN {left // 60}:{left % 60:02d} TO RETURN TO IT."),
    ]


def resume_dead(letter: str, reason: str) -> list[Line]:
    """The held game was a death: the drop came while the pod was under attack (II §13 item 3)."""
    return [
        error(f"YOUR LINE DROPPED IN CITY {letter} DURING A FIGHT ({reason})."),
        error("THAT COUNTED AS A DEATH: IT CANNOT BE RESUMED."),
    ]


def resume_launch(city: CityConfig) -> list[Line]:
    return [heading(f"RESUMING IN CITY {city.letter}, {city.name}")]


def resume_refused(*, guest: bool) -> list[Line]:
    if guest:
        return [error("/RESUME IS FOR REGISTERED ACCOUNTS: JOINGENIE LOGINS CANNOT RESUME")]
    return [error("NO GAME IS HELD FOR YOU")]


def colors() -> list[Line]:
    return [Line(f"D{n}  THIS IS COLOR {n}", n) for n in policy.USABLE_COLORS]


def unknown(word: str) -> list[Line]:
    return [error(f"UNKNOWN COMMAND: /{word}. TYPE /HELP")]


def usage(text: str) -> list[Line]:
    return [error(text)]


def denied(reason: str) -> list[Line]:
    return [error(reason)]
