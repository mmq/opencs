"""Store protocols: what the rest of the server may ask of persistence (DESIGN §3, ADR 7).

Only accounts exist so far; progress and ``/RESUME`` records join them in M10. An implementation
must be safe to call from the event loop without awaiting (the in-memory one is trivially; SQLite
is fast enough for single-row reads, DESIGN §3.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Account:
    """A real account (II §3): its name and the salted password hash.

    ``name`` is the upper-cased login name and the key. The password itself is never stored
    (DESIGN §5); ``iterations`` is kept with the hash so the cost can be raised later without
    locking anyone out.
    """

    name: str
    salt: bytes
    password_hash: bytes
    iterations: int


class AccountStore(Protocol):
    """Accounts by name."""

    def get(self, name: str) -> Account | None:
        """The account called ``name`` (already upper-cased), or None."""
        ...

    def create(self, account: Account) -> bool:
        """Store a new account; False, storing nothing, if the name is taken."""
        ...
