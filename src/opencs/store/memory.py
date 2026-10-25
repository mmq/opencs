"""In-memory stores, for tests and for a server run without a data directory (DESIGN §3)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace

from opencs.store.base import Account, ProgressRecord, Stores


class MemoryAccountStore:
    """``AccountStore`` backed by a dict; forgets everything at exit. ``now`` stamps logins."""

    def __init__(self, now: Callable[[], float] = time.time) -> None:
        self._accounts: dict[str, Account] = {}
        self._seen: dict[str, float] = {}  # created or last logged in to
        self._now = now

    def get(self, name: str) -> Account | None:
        return self._accounts.get(name)

    def create(self, account: Account) -> bool:
        if account.name in self._accounts:
            return False
        self._accounts[account.name] = account
        self._seen[account.name] = self._now()
        return True

    def callsign_registered(self, callsign: str, length: int) -> bool:
        return any(name[:length] == callsign for name in self._accounts)

    def count(self) -> int:
        return len(self._accounts)

    def touch_login(self, name: str) -> None:
        if name in self._accounts:
            self._seen[name] = self._now()

    def stale(self, before: float) -> list[str]:
        return sorted(name for name, seen in self._seen.items() if seen < before)

    def set_password(self, account: Account) -> bool:
        if account.name not in self._accounts:
            return False
        self._accounts[account.name] = account
        return True

    def delete(self, name: str) -> bool:
        self._seen.pop(name, None)
        return self._accounts.pop(name, None) is not None


class MemoryProgressStore:
    """``ProgressStore`` backed by a dict. ``now`` stamps ``updated`` (the wall clock by default;
    a test passes its own)."""

    def __init__(self, now: Callable[[], float] = time.time) -> None:
        self._records: dict[str, ProgressRecord] = {}
        self._now = now

    def load(self, identity: str) -> ProgressRecord | None:
        return self._records.get(identity)

    def save(self, identity: str, record: ProgressRecord) -> None:
        self._records[identity] = replace(record, updated=self._now())

    def erase(self, identity: str) -> bool:
        return self._records.pop(identity, None) is not None


def memory_stores() -> Stores:
    """A fresh set of in-memory stores."""
    return Stores(MemoryAccountStore(), MemoryProgressStore())
