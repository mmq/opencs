"""The Chamber: the lobby as a text session (I §5.2-§5.5, II §4).

Entering runs the version check, then the lobby commands run until a ``/PLAY`` launches
(``E <map> <map>`` -> ``/GO <status>`` -> ``C``) and the connection moves to the in-game phase.
All timers are deadlines checked in ``tick``; nothing sleeps.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from opencs import policy
from opencs.lobby import commands, replies
from opencs.lobby.lobby import Denied, Member, Placement
from opencs.session.badinput import WindowedCounter
from opencs.session.phases import TextPhase
from opencs.session.ratelimit import TokenBucket
from opencs.wire.text import arm_line

if TYPE_CHECKING:
    from opencs.session.session import Session

#: Why a launch did not happen, by the client's ``/GO`` status (I §5.4).
GO_PROBLEMS = {
    1: "THE CITY FILE FOR MAP {map} IS MISSING ON YOUR COMPUTER",
    2: "THE CITY FILE FOR MAP {map} DOES NOT MATCH THIS SERVER",
    3: "YOUR COMPUTER HAS NOT ENOUGH MEMORY TO LOAD MAP {map}",
}


#: The client's version reply (II §4.1): ``CSTTBT <n> <n>``.
_VERSION_REPLY = re.compile(r"CSTTBT \d+ \d+")


class State(Enum):
    """Where the Chamber is."""

    VERSION = auto()  # sent PRESS RETURN, waiting for the reply
    LOBBY = auto()
    LAUNCH = auto()  # sent E <map> <map>, waiting for /GO


class ChamberPhase(TextPhase):
    """Version check, lobby commands, chat relay and the launch."""

    def __init__(self, host: Session) -> None:
        super().__init__(host)
        self._state = State.VERSION
        self._deadline = 0.0
        self._member: Member | None = None
        self._placement: Placement | None = None
        self._city_list_at: float | None = None
        self._handlers = self._handler_table()
        self._late_reply_possible = False
        self._bucket = TokenBucket(
            policy.CHAMBER_BURST_LINES, policy.CHAMBER_LINES_PER_SECOND, host.now()
        )
        self._drops = WindowedCounter(policy.CHAMBER_FLOOD_DROPS, policy.CHAMBER_FLOOD_WINDOW)
        self._warned = False

    @property
    def member(self) -> Member:
        assert self._member is not None
        return self._member

    def enter(self) -> None:
        self._member = self.host.join_lobby()
        self._deadline = self.host.now() + policy.VERSION_WAIT
        self.host.io.send_raw(arm_line("PRESS RETURN"))

    # --- time ------------------------------------------------------------------------------

    def tick(self, now: float) -> None:
        if self._state is State.VERSION and now >= self._deadline:
            self.host.log.info("no version reply in %.0f s; entering anyway", policy.VERSION_WAIT)
            self._open_lobby()
            self._late_reply_possible = True
        elif self._state is State.LAUNCH and now >= self._deadline:
            self.host.log.info("no /GO reply in %.0f s; staying in the Chamber", policy.GO_WAIT)
            self._state = State.LOBBY
            self._placement = None
            self._say(replies.denied("NO REPLY TO THE LAUNCH REQUEST: STAYING IN THE CHAMBER"))

    # --- lines -----------------------------------------------------------------------------

    def on_overlong(self) -> None:
        if self._state is State.LOBBY:
            self._say(replies.denied("THAT LINE IS TOO LONG"))

    def on_line(self, line: str) -> None:
        if self._state is State.VERSION:
            # Whatever comes back (CSTTBT 105 150, a bare CR) is logged and never refused.
            self.host.log.info("version reply: %r", line)
            self._open_lobby()
            return
        if self._state is State.LOBBY and (
            self._swallow_late_reply(line) or not self._within_rate()
        ):
            return
        command = commands.parse(line)
        if self._state is State.LAUNCH:
            self._launch_line(command)
        else:
            self._lobby_line(command)

    def _swallow_late_reply(self, line: str) -> bool:
        """A version reply that arrives after the timeout is not chat: drop the first line after
        entering if it is one (II §4.1)."""
        possible, self._late_reply_possible = self._late_reply_possible, False
        return possible and bool(_VERSION_REPLY.fullmatch(line.strip()))

    def _within_rate(self) -> bool:
        """Spend a token for this line; over the limit, drop it, warn once, and close a flood
        (DESIGN §5)."""
        now = self.host.now()
        if self._bucket.take(now):
            self._warned = False
            return True
        if self._drops.record(now):
            self.host.log.warning("closing: Chamber flood")
            self.host.close("Chamber flood")
        elif not self._warned:
            self._warned = True
            self._say(replies.denied("TOO MANY LINES: SLOW DOWN"))
        return False

    def _open_lobby(self) -> None:
        self._state = State.LOBBY
        self.host.io.send_raw(arm_line("A") + replies.render(replies.welcome(self.member.callsign)))

    def _say(self, lines: list[replies.Line], *, restore_buttons: bool = False) -> None:
        data = replies.render(lines)
        if restore_buttons:
            data += arm_line("A")
        self.host.io.send_raw(data)

    def _lobby_line(self, command: commands.Command) -> None:
        self._handlers[type(command)](command)

    def _handler_table(self) -> dict[type, Callable[[Any], None]]:
        """The method that answers each command type."""
        lobby = self.host.services.lobby
        return {
            commands.Nothing: lambda c: None,
            commands.Say: self._chat,
            commands.BareLetter: lambda c: self._bare_letter(c.letter, c.text),
            commands.Help: lambda c: self._say(replies.help_lines(), restore_buttons=True),
            commands.CityList: self._city_list,
            commands.CityInfo: self._city_info,
            commands.Status: lambda c: self._say(replies.status(lobby)),
            commands.WhoIs: lambda c: self._say(replies.who_is(lobby.find(c.callsign), c.callsign)),
            commands.Score: lambda c: self._say(replies.score()),
            commands.Play: lambda c: self._play(c.letter, c.team),
            commands.Resume: lambda c: self._say(
                replies.resume_refused(guest=self.member.account is None)
            ),
            commands.Colors: lambda c: self._say(replies.colors()),
            commands.Quit: lambda c: self._quit(),
            commands.Go: lambda c: self.host.log.info(
                "ignoring /GO %d: no launch is waiting", c.status
            ),
            commands.Unknown: lambda c: self._say(replies.unknown(c.word)),
            commands.Usage: lambda c: self._say(replies.usage(c.text)),
        }

    def _chat(self, command: commands.Say) -> None:
        self.host.services.lobby.relay_chat(self.member, command.text)

    def _city_list(self, command: commands.CityList) -> None:
        self._city_list_at = self.host.now()
        self._say(replies.city_list(self.host.services.lobby, command.draft))

    def _city_info(self, command: commands.CityInfo) -> None:
        lobby = self.host.services.lobby
        city = lobby.city(command.letter)
        if city is None:
            self._say(replies.denied(f"NO SUCH CITY: {command.letter}"))
        else:
            self._say(replies.city_info(lobby, city))

    def _bare_letter(self, letter: str, text: str) -> None:
        """A lone letter shortly after a bare ``/CITY`` is the city button's second half with its
        prefix lost: answer with a hint instead of chatting it (II §4.2)."""
        at = self._city_list_at
        recent = at is not None and self.host.now() - at <= policy.CITY_HINT_WINDOW
        if recent and self.host.services.lobby.city(letter) is not None:
            self._say(replies.city_hint(letter))
        else:
            self.host.services.lobby.relay_chat(self.member, text)

    def _quit(self) -> None:
        """``/QUIT``: GEnie's buttons come back and the ``*`` prompt (II §4.1)."""
        self.host.leave_lobby()
        self.host.io.send_raw(arm_line("B"))
        self.host.to_login_prompt()

    # --- launch ----------------------------------------------------------------------------

    def _play(self, letter: str | None, team: int | None) -> None:
        result = self.host.services.lobby.place(letter, team)
        if isinstance(result, Denied):
            self._say(replies.denied(result.reason))
            return
        self._placement = result
        self._state = State.LAUNCH
        self._deadline = self.host.now() + policy.GO_WAIT
        lines = [replies.body(result.note)] if result.note else []
        self.host.io.send_raw(
            replies.render(lines) + arm_line(f"E {result.city.map} {result.city.map}")
        )

    def _launch_line(self, command: commands.Command) -> None:
        placement = self._placement
        assert placement is not None
        if not isinstance(command, commands.Go):
            if not isinstance(command, commands.Nothing):
                self._say(replies.denied("LAUNCH IN PROGRESS"))
            return
        self._state = State.LOBBY
        self._placement = None
        if command.status == 0:
            if self.host.services.lobby.enter(self.member, placement):
                self.host.log.info(
                    "launching into city %s team %s", placement.city.letter, placement.team
                )
                self.host.io.send_raw(arm_line("C"))
                self.host.to_ingame()
            else:
                self._say(replies.denied(f"CITY {placement.city.letter} FILLED UP: TRY AGAIN"))
        elif command.status in GO_PROBLEMS:
            problem = GO_PROBLEMS[command.status].format(map=placement.city.map)
            self._say(replies.denied(problem))
        else:
            self._say(replies.denied(f"UNKNOWN LAUNCH STATUS {command.status}"))
