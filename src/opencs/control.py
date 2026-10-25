"""What an operator can do to the running server (DESIGN §6): the services behind the admin
channel, the signal handlers and shutdown.

Everything here runs on the event loop and is synchronous, like the world: it reads and changes
the same objects the sessions do, so no lock is needed. ``Control.execute`` takes one command line
and returns the lines to show; the admin channel (the I/O) only authenticates, bounds and carries
them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from opencs import policy
from opencs.cities import CityManager
from opencs.config import ConfigFile
from opencs.connlog import STREAM_CHAMBER, STREAM_GAME
from opencs.lobby import replies
from opencs.lobby.identity import Refused, identity_key
from opencs.messages import server
from opencs.routing import Router
from opencs.session.phases import Services
from opencs.world import chat

log = logging.getLogger("opencs.admin")

#: The notice players get when the server is going down (ours).
SHUTDOWN_NOTICE = policy.SHUTDOWN_NOTICE

HELP = (
    "status                      connections, players per city and team, held games, uptime",
    "announce <text>             a system line to every player (in game and in the Chamber)",
    "kick <callsign> [reason]    take a pilot out of its game (back to the Chamber), or off",
    "guppy <team> home           warp a stranded team guppy home and reset its leg counts",
    "shutdown [seconds|cancel]   announce, then stop the server gracefully (saving everyone)",
    "reload                      re-read the city files (a running game is never changed)",
    "accounts                    how many accounts there are, and whether first logins register",
    "create-account <name>       make an account with a new password, shown once",
    "reset-password <name>       give an account a new password, shown once",
    "reset-progress <name>       wipe an account's saved progress (it must be offline)",
    "delete-account <name>       remove an account and its progress (it must be offline)",
    "help                        this list",
    "quit                        close this connection",
)


#: The words ``Control.execute`` answers; the admin channel logs a word only if it is one of these,
#: so a token typed again after logging in (an unknown word) never reaches the log.
COMMAND_NAMES = (
    "status",
    "announce",
    "kick",
    "guppy",
    "shutdown",
    "reload",
    "help",
    "accounts",
    "create-account",
    "reset-password",
    "reset-progress",
    "delete-account",
)


def announce(services: Services, text: str, router: Router | None = None) -> int:
    """Put ``text`` on every player's screen: an ``'a'`` from sender 100 (I §8.23) to each client
    in a game that has started playing, a plain line to each pilot in the Chamber. The text is
    cut to what the client accepts and to printable ASCII. Returns how many received it."""
    router = router or services.router
    clean = server.sanitize_chat(text.encode("ascii", "replace")).decode("ascii")
    received = 0
    for city in services.cities.values():
        audience = city.playing()
        router.deliver(city, [chat.system_line(clean, audience)])
        received += len(router.resolve(city, audience))
    line = replies.render([replies.body(f"*** {clean}")])
    for member in services.lobby.chamber():
        member.deliver(line)
        received += 1
    return received


def remove_everyone(services: Services, notice: str) -> int:
    """Shutdown: end every session, a game with the unprompted ``'C'`` and a leave that saves the
    player (``Session.remove``), anything else with a line and a close. Returns how many."""
    sessions = services.sessions.all()
    for session in sessions:
        try:
            session.remove(notice, close=True)
        except Exception:
            # One session failing must not keep the others from being saved and closed.
            log.exception("could not remove a session at shutdown")
    return len(sessions)


def parse_team(text: str) -> int | None:
    """A team by name (``RED``) or number (``0``), None if it is neither."""
    word = text.upper()
    if word in policy.TEAM_NAMES:
        return policy.TEAM_NAMES.index(word)
    if word.isascii() and word.isdigit() and int(word) < policy.TEAM_COUNT:
        return int(word)
    return None


class Control:
    """The operator's commands over a running server."""

    def __init__(
        self,
        services: Services,
        cities: CityManager,
        stop: asyncio.Event,
        config_file: ConfigFile | None,
        *,
        delay: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._services = services
        self._cities = cities
        self._stop = stop
        self._config_file = config_file
        self._delay = delay
        self._started = services.clock.now()
        self._countdown: asyncio.Task[None] | None = None

    def close(self) -> None:
        """Cancel a pending ``shutdown <seconds>`` (the server is stopping anyway)."""
        if self._countdown is not None:
            self._countdown.cancel()
            self._countdown = None

    # --- one line ----------------------------------------------------------------------------

    def execute(self, line: str) -> list[str]:
        """Run one command line; the lines to show. Never raises on operator input."""
        word, _, rest = line.strip().partition(" ")
        rest = rest.strip()
        commands: dict[str, Callable[[], list[str]]] = {
            "status": self.status,
            "announce": lambda: self.announce(rest),
            "kick": lambda: self.kick(rest),
            "guppy": lambda: self.guppy(rest),
            "shutdown": lambda: self.shutdown(rest),
            "reload": self.reload,
            "help": lambda: list(HELP),
            "accounts": self.accounts,
            "create-account": lambda: self.create_account(rest),
            "reset-password": lambda: self.reset_password(rest),
            "reset-progress": lambda: self.reset_progress(rest),
            "delete-account": lambda: self.delete_account(rest),
        }
        if not word:
            return []
        command = commands.get(word.lower())
        return [f"unknown command {word!r}: try help"] if command is None else command()

    # --- commands ----------------------------------------------------------------------------

    def status(self) -> list[str]:
        services = self._services
        sessions = services.sessions.all()
        streams = [s.log.context.stream for s in sessions]
        lines = [
            f"uptime: {services.clock.now() - self._started:.0f} s",
            f"connections: {len(sessions)} (in game {streams.count(STREAM_GAME)}, "
            f"Chamber {streams.count(STREAM_CHAMBER)}, "
            f"logon {len(sessions) - streams.count(STREAM_GAME) - streams.count(STREAM_CHAMBER)})",
        ]
        for letter, city in sorted(services.cities.items()):
            teams = ", ".join(
                f"{name} {count}"
                for team, name in enumerate(policy.TEAM_NAMES)
                if (count := len(services.lobby.team_members(letter, team)))
            )
            offered = services.lobby.city(letter) is not None
            lines.append(
                f"city {letter} {city.config.name}: {services.lobby.count(letter)} players"
                f"{f' ({teams})' if teams else ''}"
                f"{'' if offered else ' [closing: no new players]'}"
            )
        held = services.held.live()
        lines.append(f"held games: {len(held)}")
        lines += (
            f"  {r.callsign} city {r.city} {policy.TEAM_NAMES[r.team]}, {left:.0f} s left, "
            f"{'can be resumed' if r.eligible else 'counted as a death'}"
            for r, left in held
        )
        return lines

    # --- accounts (a password made here is shown once and never logged) ---------------------------

    def accounts(self) -> list[str]:
        identity = self._services.identity
        mode = "open" if identity.registration_open else "closed"
        return [
            f"accounts: {identity.account_count()} of at most {identity.max_accounts}; "
            f"registration {mode}"
        ]

    def create_account(self, name: str) -> list[str]:
        if not name or " " in name:
            return ["usage: create-account <name>"]
        result = self._services.identity.register(name)
        if isinstance(result, Refused):
            return [f"refused: {result.reason}"]
        account, password = result
        log.info("account %s created by the operator", account.name)
        return [f"created {account.name}; password (shown once): {password}"]

    def reset_password(self, name: str) -> list[str]:
        if not name or " " in name:
            return ["usage: reset-password <name>"]
        result = self._services.identity.reset_password(name)
        if isinstance(result, Refused):
            return [f"refused: {result.reason}"]
        log.info("password of %s reset by the operator", name.upper())
        return [f"new password for {name.upper()} (shown once): {result}"]

    def reset_progress(self, name: str) -> list[str]:
        if not name or " " in name:
            return ["usage: reset-progress <name>"]
        name = name.upper()
        if (refusal := self._offline_account(name)) is not None:
            return [refusal]
        erased = self._services.persistence.erase(identity_key(name, name))
        log.info("progress of %s reset by the operator", name)
        return [f"progress of {name} {'erased' if erased else 'was empty'}"]

    def delete_account(self, name: str) -> list[str]:
        if not name or " " in name:
            return ["usage: delete-account <name>"]
        name = name.upper()
        if (refusal := self._offline_account(name)) is not None:
            return [refusal]
        self._services.identity.delete_account(name)
        self._services.persistence.erase(identity_key(name, name))
        log.info("account %s deleted by the operator", name)
        return [f"deleted {name}"]

    def _offline_account(self, name: str) -> str | None:
        """Why account ``name`` cannot be changed now, None if it can."""
        identity = self._services.identity
        if identity.account_online(name):
            return f"refused: {name} is online; kick it first"
        if self._services.held.get(identity_key(name, name)) is not None:
            return f"refused: {name} has a game held for /RESUME; wait for it to expire"
        if not identity.account_exists(name):
            return f"refused: no account {name}"
        return None

    def announce(self, text: str) -> list[str]:
        if not text:
            return ["usage: announce <text>"]
        received = announce(self._services, text)
        log.info("announce to %d: %s", received, text)
        return [f"announced to {received}"]

    def kick(self, args: str) -> list[str]:
        callsign, _, reason = args.partition(" ")
        if not callsign:
            return ["usage: kick <callsign> [reason]"]
        reason = reason.strip()
        notice = f"{policy.ADMIN_KICK_NOTICE}: {reason}" if reason else policy.ADMIN_KICK_NOTICE
        sessions = self._services.sessions.by_callsign(callsign.upper())
        if not sessions:
            return [f"no pilot {callsign.upper()} is connected"]
        for session in sessions:
            session.remove(notice, close=False)
        log.info("kicked %s: %s", callsign.upper(), reason or "no reason given")
        return [f"kicked {callsign.upper()} ({len(sessions)} connection(s))"]

    def guppy(self, args: str) -> list[str]:
        words = args.split()
        team = parse_team(words[0]) if words else None
        if team is None or words[1:] != ["home"]:
            return [f"usage: guppy <{'|'.join(policy.TEAM_NAMES)}|0-3> home"]
        now = self._services.clock.now()
        lines = []
        for letter, city in sorted(self._services.cities.items()):
            events = city.guppy_home(team, now)
            if events is not None:
                self._services.router.deliver(city, events)
                lines.append(
                    f"city {letter}: {policy.TEAM_NAMES[team]} guppy warped home, "
                    f"{len(events)} client(s) told"
                )
        log.info("guppy %s home: %s", policy.TEAM_NAMES[team], lines or "no guppy")
        return lines or [f"no {policy.TEAM_NAMES[team]} guppy exists in any city"]

    def shutdown(self, arg: str) -> list[str]:
        if arg == "cancel":
            if self._countdown is None:
                return ["no shutdown is pending"]
            self.close()
            announce(self._services, policy.SHUTDOWN_CANCELED_NOTICE)
            return ["shutdown canceled"]
        if arg and not (arg.isascii() and arg.isdigit()):
            return ["usage: shutdown [seconds|cancel]"]
        seconds = int(arg) if arg else 0
        if seconds > policy.ADMIN_MAX_SHUTDOWN_SECONDS:
            return [f"at most {policy.ADMIN_MAX_SHUTDOWN_SECONDS} seconds"]
        self.close()
        if seconds == 0:
            log.info("shutdown requested")
            self._stop.set()
            return ["shutting down now"]
        announce(self._services, f"{SHUTDOWN_NOTICE} IN {seconds} SECONDS")
        self._countdown = asyncio.create_task(self._count_down(seconds))
        return [f"shutting down in {seconds} s"]

    def reload(self) -> list[str]:
        if self._config_file is None:
            return ["no config file is known: nothing to reload"]
        return self._cities.reload_file(self._config_file.path, self._config_file.server)

    async def _count_down(self, seconds: int) -> None:
        await self._delay(seconds)
        log.info("shutdown countdown over")
        self._stop.set()
