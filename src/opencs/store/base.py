"""Store protocols: what the rest of the server may ask of persistence (DESIGN §3, ADR 7).

Accounts and progress live here. ``/RESUME`` records are held in memory by the lobby, not stored
(II §13: a restart loses held games). An implementation must be safe to call from the event
loop without awaiting (the in-memory one is trivially; SQLite is fast enough for single-row reads
and writes, DESIGN §3.3).
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

    def callsign_registered(self, callsign: str, length: int) -> bool:
        """True if some account's name cut to ``length`` characters equals ``callsign``: that
        callsign belongs to the account, so no guest may take it (II §3)."""
        ...

    def count(self) -> int:
        """How many accounts there are (``policy.MAX_ACCOUNTS``)."""
        ...

    def touch_login(self, name: str) -> None:
        """Note that ``name`` has just logged in (it is not stale)."""
        ...

    def stale(self, before: float) -> list[str]:
        """The accounts neither created nor logged in to since ``before`` (wall-clock seconds)."""
        ...

    def set_password(self, account: Account) -> bool:
        """Replace the salt, hash and work factor of an existing account; False if there is none."""
        ...

    def delete(self, name: str) -> bool:
        """Remove the account; False if there was none. Its progress is the caller's to delete."""
        ...


@dataclass(frozen=True, slots=True)
class ProgressRecord:
    """A player's persistent record (II §11.3): the nine bay levels, the battle marker, the bay
    points, the SCORE of the last (or current) game, and the ``career`` total of the games before
    it (II §11.3, D2). The multiplier is never kept: it restarts at 1 each game.

    ``updated`` is the store's own wall-clock stamp of the last save; whatever a caller puts in it
    is replaced on ``save``.
    """

    bays: tuple[int, ...]
    marker: int
    bay_points: int
    score: int
    career: int
    updated: float = 0.0


class ProgressStore(Protocol):
    """Progress records by identity key (``lobby.identity.identity_key``)."""

    def load(self, identity: str) -> ProgressRecord | None:
        """The record for ``identity``, or None if it has never been saved."""
        ...

    def save(self, identity: str, record: ProgressRecord) -> None:
        """Store ``record`` as the whole record of ``identity``, atomically."""
        ...

    def erase(self, identity: str) -> bool:
        """Remove the record of ``identity``; False if there was none."""
        ...


@dataclass(frozen=True, slots=True)
class Stores:
    """Every store the server uses, built together so one place owns their lifetime."""

    accounts: AccountStore
    progress: ProgressStore
