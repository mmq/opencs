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
from opencs.connlog import STREAM_CHAMBER
from opencs.lobby import commands, replies
from opencs.lobby.lobby import Denied, Member, Placement
from opencs.session.phases import TextPhase
from opencs.wire.text import arm_line
from opencs.world.resume import ResumeRecord

if TYPE_CHECKING:
    from opencs.session.session import Session

#: Why a launch did not happen, by the client's ``/GO`` status (I §5.4).
GO_PROBLEMS = {
    1: "THE CITY FILE FOR MAP {map} IS MISSING ON YOUR COMPUTER",
    2: "THE CITY FILE FOR MAP {map} DOES NOT MATCH THIS SERVER",
    3: "YOUR COMPUTER HAS NOT ENOUGH MEMORY TO LOAD MAP {map}",
}


#: What to show when the store cannot be read: the player is told, the server carries on.
_UNREADABLE = replies.denied("THE RECORD CANNOT BE READ RIGHT NOW")

#: The client's version reply (II §4.1): ``CSTTBT <n> <n>``.
_VERSION_REPLY = re.compile(r"CSTTBT \d+ \d+")


class State(Enum):
    """Where the Chamber is."""

    VERSION = auto()  # sent PRESS RETURN, waiting for the reply
    LOBBY = auto()
    LAUNCH = auto()  # sent E <map> <map>, waiting for /GO


class ChamberPhase(TextPhase):
    """Version check, lobby commands, chat relay and the launch."""

    STREAM = STREAM_CHAMBER

    def __init__(self, host: Session, *, returning: bool = False, late_quits: int = 0) -> None:
        super().__init__(host)
        self._returning = returning
        self._late_quits = late_quits
        self._late_quit_until = 0.0
        self._state = State.VERSION
        self._deadline = 0.0
        self._member: Member | None = None
        self._placement: Placement | None = None
        #: The held game a launch in progress is returning to (II §13 item 5).
        self._resuming: ResumeRecord | None = None
        self._city_list_at: float | None = None
        self._handlers = self._handler_table()
        self._late_reply_possible = False
        # The session's, so leaving and re-entering the Chamber does not refill them.
        self._bucket = host.budgets.chamber_lines
        self._drops = host.budgets.chamber_drops
        self._warned = False
        self._last_input_at = host.now()

    @property
    def member(self) -> Member:
        assert self._member is not None
        return self._member

    def enter(self) -> None:
        if self._returning:
            # Back from a game: the version check is not repeated (the Chamber program starts
            # silent, I §5.5); the buttons come back and a stray version reply is swallowed.
            self._member = self.host.member
            self._late_reply_possible = True
            self._late_quit_until = self.host.now() + policy.LATE_QUIT_WINDOW
            self._open_lobby()
            if self.host.notice is not None:  # the server ended the game (I §5.5)
                self._say(replies.denied(self.host.notice))
                self.host.notice = None
            return
        self._member = self.host.join_lobby()
        self._deadline = self.host.now() + policy.VERSION_WAIT
        self.host.io.send_raw(arm_line("PRESS RETURN"))

    # --- time ------------------------------------------------------------------------------

    def tick(self, now: float) -> None:
        if now - self._last_input_at >= self._idle_timeout():
            self.host.log.info("nothing heard for %.0f s; closing", now - self._last_input_at)
            self._say(replies.denied("IDLE TOO LONG: DISCONNECTING"))
            self.host.close("Chamber idle timeout")
            return
        if self._state is State.VERSION and now >= self._deadline:
            self.host.log.info("no version reply in %.0f s; entering anyway", policy.VERSION_WAIT)
            self._open_lobby()
            self._late_reply_possible = True
        elif self._state is State.LAUNCH and now >= self._deadline:
            self.host.log.info("no /GO reply in %.0f s; staying in the Chamber", policy.GO_WAIT)
            self._state = State.LOBBY
            self._placement = self._resuming = None
            self._say(replies.denied("NO REPLY TO THE LAUNCH REQUEST: STAYING IN THE CHAMBER"))

    # --- lines -----------------------------------------------------------------------------

    def on_overlong(self) -> None:
        """An overlong line is charged to the line bucket like any other, so a stream of them
        closes the connection as a flood does (DESIGN §5); within the budget it is refused."""
        if self._state is State.LOBBY and self._within_rate():
            self._say(replies.denied("THAT LINE IS TOO LONG"))

    def _idle_timeout(self) -> float:
        """How long the Chamber waits for a line: a guest, who has nothing saved to come back to
        and costs nothing to make, waits less (``policy.GUEST_CHAMBER_IDLE_TIMEOUT``)."""
        if self._member is not None and self._member.account is None:
            return policy.GUEST_CHAMBER_IDLE_TIMEOUT
        return policy.CHAMBER_IDLE_TIMEOUT

    def on_line(self, line: str) -> None:
        if line.strip():  # only a line restarts the idle clock, not bytes the reader drops
            self._last_input_at = self.host.now()
        if self._state is State.VERSION:
            # Whatever comes back (CSTTBT 105 150, a bare CR) is logged and never refused.
            self.host.log.info("version reply: %r", line)
            self._open_lobby()
            return
        if self._state is State.LOBBY and (
            self._swallow_late_quit(line)
            or self._swallow_late_reply(line)
            or not self._within_rate()
        ):
            return
        command = commands.parse(line)
        if self._state is State.LAUNCH:
            self._launch_line(command)
        else:
            self._lobby_line(command)

    def _swallow_late_quit(self, line: str) -> bool:
        """A ``QUIT`` the client sent as part of its exit, arriving after we returned, is not chat
        (I §5.5 step 4): drop up to the number still owed, for a short while."""
        if self._late_quits and line.strip() == "QUIT" and self.host.now() < self._late_quit_until:
            self._late_quits -= 1
            return True
        return False

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
        self._tell_held_game()

    def _tell_held_game(self) -> None:
        """Back in the Chamber with a game held for this account: say so, which starts the
        window in which ``/RESUME`` may be typed (II §13 item 4)."""
        if self.member.account is None:
            return
        held = self.host.services.held
        record = held.entered_chamber(self.member.key)
        if record is None:
            return
        if record.eligible:
            self._say(replies.resume_held(record.city, held.seconds_left(self.member.key)))
        else:
            self._say(replies.resume_dead(record.city, record.reason))

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
            commands.WhoIs: self._who_is,
            commands.Score: self._score,
            commands.Play: lambda c: self._play(c.letter, c.team),
            commands.Resume: lambda c: self._resume(),
            commands.Colors: lambda c: self._say(replies.colors()),
            commands.Quit: lambda c: self._quit(),
            commands.Go: lambda c: self.host.log.info(
                "ignoring /GO %d: no launch is waiting", c.status
            ),
            commands.Unknown: lambda c: self._say(replies.unknown(c.word)),
            commands.Usage: lambda c: self._say(replies.usage(c.text)),
        }

    def _who_is(self, command: commands.WhoIs) -> None:
        """``/WHO <callsign>``: where the pilot is, and its record (II §4.2)."""
        found = self.host.services.lobby.find(command.callsign)
        if found is None:
            self._say(replies.who_is(None, command.callsign, None))
            return
        if found.account is None:
            self._say(replies.who_is_guest(found))
            return
        try:
            record = self.host.services.persistence.record(found.key)
        except Exception:
            self.host.log.exception("could not read the record of %s", found.key)
            self._say([replies.body(f"{found.callsign}: {replies.where(found)}"), *_UNREADABLE])
            return
        self._say(replies.who_is(found, command.callsign, record))

    def _score(self, command: commands.Score) -> None:
        """``/SCORE``: the player's own record (II §4.2)."""
        if self.member.account is None:
            self._say(replies.score_guest())
            return
        try:
            record = self.host.services.persistence.record(self.member.key)
        except Exception:
            self.host.log.exception("could not read the record of %s", self.member.key)
            self._say(_UNREADABLE)
            return
        self._say(replies.score(self.member.callsign, record))

    def _chat(self, command: commands.Say) -> None:
        self._relay(command.text)

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
            self._relay(text)

    def _relay(self, text: str) -> None:
        """Relay a line of chat, within the budget of the sender's address, which its other
        connections share (``policy.CHAT_PER_ADDRESS``)."""
        if not self.host.services.chat_limits.take(self.host.address, self.host.now()):
            if not self._warned:
                self._warned = True
                self._say(replies.denied("TOO MANY LINES: SLOW DOWN"))
            return
        self.host.services.lobby.relay_chat(self.member, text)

    def _quit(self) -> None:
        """``/QUIT``: GEnie's buttons come back and the ``*`` prompt (II §4.1)."""
        if not self.host.budgets.reentries.take(self.host.now()):
            self.host.log.warning("closing: left the Chamber too often")
            self.host.close("Chamber re-entry flood")
            return
        self.host.leave_lobby()
        self.host.io.send_raw(arm_line("B"))
        self.host.to_login_prompt()

    # --- launch ----------------------------------------------------------------------------

    def _resume(self) -> None:
        """``/RESUME``: launch into the held city and team (II §4.2, II §13, II §13 items 4 and 5).
        The record stays held until the game takes it, so a launch that fails can be tried again."""
        if self.member.account is None:
            self._say(replies.resume_refused(guest=True))
            return
        held = self.host.services.held
        record = held.get(self.member.key)
        if record is None:
            self._say(replies.resume_refused(guest=False))
            return
        if not record.eligible:
            held.discard(self.member.key)
            self._say(replies.resume_dead(record.city, record.reason))
            return
        city = self.host.services.lobby.city(record.city)
        if city is None:
            self._say(replies.denied(f"CITY {record.city} IS CLOSED: THE GAME CANNOT BE RESUMED"))
            return
        self._resuming = record
        self._begin_launch(Placement(city, record.team), replies.resume_launch(city))

    def _begin_launch(self, placement: Placement, lines: list[replies.Line]) -> None:
        """Ask the client to select the city (I §5.4) and wait for its ``/GO``."""
        self._placement = placement
        self._state = State.LAUNCH
        self._deadline = self.host.now() + policy.GO_WAIT
        city = placement.city
        self.host.io.send_raw(replies.render(lines) + arm_line(f"E {city.map} {city.map}"))

    def _play(self, letter: str | None, team: int | None) -> None:
        result = self.host.services.lobby.place(letter, team, exclude=self.member.key)
        if isinstance(result, Denied):
            self._say(replies.denied(result.reason))
            return
        lines = [replies.body(result.note)] if result.note else []
        if self.host.services.held.discard(self.member.key) is not None:
            lines.insert(0, replies.body("YOUR HELD GAME IS GIVEN UP: STARTING A NEW ONE"))
        self._begin_launch(result, lines)

    def _launch_line(self, command: commands.Command) -> None:
        placement = self._placement
        assert placement is not None
        if not isinstance(command, commands.Go):
            if not isinstance(command, commands.Nothing):
                self._say(replies.denied("LAUNCH IN PROGRESS"))
            return
        self._state = State.LOBBY
        resuming, self._resuming = self._resuming, None
        self._placement = None
        if command.status == 0:
            if self.host.services.lobby.enter(self.member, placement):
                self.host.log.info(
                    "launching into city %s team %s%s",
                    placement.city.letter,
                    placement.team,
                    " (resuming)" if resuming else "",
                )
                if resuming is not None:
                    # The game takes the record; the join below needs its slot to be the player's.
                    self.host.services.held.discard(resuming.identity)
                    self.host.resuming = resuming
                self.host.io.send_raw(arm_line("C"))
                self.host.to_ingame()
            else:
                self._say(replies.denied(f"CITY {placement.city.letter} FILLED UP: TRY AGAIN"))
        elif command.status in GO_PROBLEMS:
            problem = GO_PROBLEMS[command.status].format(map=placement.city.map)
            self._say(replies.denied(problem))
        else:
            self._say(replies.denied(f"UNKNOWN LAUNCH STATUS {command.status}"))
