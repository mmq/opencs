"""Every live session, for the operator (DESIGN §6): who is connected, and who to kick.

The registry is the session layer's own list, so the admin channel (which is I/O and sits beside
``net``) reaches sessions without the net layer knowing of players or the session layer of sockets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opencs.session.session import Session


class SessionRegistry:
    """The sessions that have not ended, oldest first."""

    def __init__(self) -> None:
        self._sessions: list[Session] = []

    def add(self, session: Session) -> None:
        self._sessions.append(session)

    def remove(self, session: Session) -> None:
        if session in self._sessions:
            self._sessions.remove(session)

    def all(self) -> list[Session]:
        """A copy, so a caller may close sessions while walking it."""
        return list(self._sessions)

    def by_callsign(self, callsign: str) -> list[Session]:
        """The sessions whose pilot has this (upper-case) callsign."""
        return [s for s in self._sessions if s.pilot is not None and s.pilot.callsign == callsign]
