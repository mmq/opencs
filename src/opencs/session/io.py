"""What a session may do to its connection: the sink it is given (DESIGN §3.4).

A session is sans-IO. It never touches a socket or ``asyncio``; the net layer's ``Connection``
implements this protocol and the tests supply a recording fake.
"""

from __future__ import annotations

from typing import Protocol

from opencs.messages import ServerMessage


class SessionIO(Protocol):
    """Output side of one connection."""

    def send_raw(self, data: bytes) -> None:
        """Queue Chamber text or other bytes exactly as given."""
        ...

    def send(self, msg: ServerMessage) -> None:
        """Queue an in-game message; the connection assigns its tag and frames it (I §4.2)."""
        ...

    def close(self, reason: str) -> None:
        """Flush what is queued, then close the connection."""
        ...
