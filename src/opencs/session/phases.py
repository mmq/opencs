"""The phase machine's parts: the ``Phase`` interface, the text-phase base and shared services.

A connection moves ``Login -> Chamber -> InGame -> Chamber`` (I §7.8). Exactly one phase owns
the inbound bytes at a time. When a phase switches, the bytes it has not yet looked at are handed
to the next one unchanged: the client starts sending binary frames right after the Chamber's ``C``
(I §5.4), and the bytes after a line belong to whoever the line switched to.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from opencs import policy
from opencs.clock import Clock
from opencs.connlog import STREAM_LOGIN
from opencs.lobby.identity import Identity
from opencs.lobby.lobby import Lobby
from opencs.routing import Router
from opencs.session.held import HeldGames
from opencs.session.loginlimits import LoginLimits
from opencs.session.persistence import Persistence
from opencs.session.ratelimit import KeyedBuckets
from opencs.session.registry import SessionRegistry
from opencs.wire.text import LineReader
from opencs.world.city import City

if TYPE_CHECKING:
    from opencs.session.session import Session

CR = b"\r"


@dataclass(frozen=True, slots=True)
class Services:
    """What every session shares."""

    identity: Identity
    lobby: Lobby
    clock: Clock
    cities: MutableMapping[str, City]  # the world, by city letter (cities.py adds and removes)
    router: Router
    persistence: Persistence
    held: HeldGames  # games held for players whose line dropped (II §13)
    #: Password checks and new accounts, metered across connections (DESIGN §5).
    login_limits: LoginLimits
    #: Every live session, for the admin channel (DESIGN §6).
    sessions: SessionRegistry = field(default_factory=SessionRegistry)
    #: Chat, per source address, over all of that address's connections (DESIGN §5).
    chat_limits: KeyedBuckets = field(
        default_factory=lambda: KeyedBuckets(*policy.CHAT_PER_ADDRESS)
    )


class Phase(ABC):
    """One phase of a connection. It owns the inbound bytes while it is the current phase."""

    #: What this phase's bytes are (``connlog.STREAM_*``); the logon until a phase says otherwise.
    STREAM: ClassVar[str] = STREAM_LOGIN

    def __init__(self, host: Session) -> None:
        self.host = host

    @abstractmethod
    def enter(self) -> None:
        """Called once, when this phase becomes the current one."""

    @abstractmethod
    def feed(self, data: bytes) -> None:
        """Bytes from the socket (or handed over by the previous phase)."""

    def tick(self, now: float) -> None:  # noqa: B027  (optional hook)
        """Time has passed: check deadlines. ``now`` is on the session clock."""

    def closed(self) -> None:  # noqa: B027  (optional hook)
        """The socket ended. The session releases the pilot and lobby member itself."""


class TextPhase(Phase):
    """A phase that reads CR-terminated lines (I §5.3).

    Lines are taken one at a time, so that when a line switches the phase the bytes after it are
    still raw and go to the next phase untouched.
    """

    def __init__(self, host: Session) -> None:
        super().__init__(host)
        self._lines = LineReader()
        self._overlong_seen = 0

    @abstractmethod
    def on_line(self, line: str) -> None:
        """One complete line, printable ASCII, without its terminator (possibly empty)."""

    def on_overlong(self) -> None:
        """A line exceeded ``policy.MAX_LINE_BYTES`` and was discarded."""

    def feed(self, data: bytes) -> None:
        while data and self.host.active:
            cr = data.find(CR)
            chunk, data = (data, b"") if cr < 0 else (data[: cr + 1], data[cr + 1 :])
            for line in self._lines.feed(chunk):
                self.on_line(line.decode("ascii"))
            if self._lines.overlong_lines != self._overlong_seen:
                self._overlong_seen = self._lines.overlong_lines
                self.on_overlong()
            if self.host.phase is not self:
                self.host.hand_over(data)
                return
