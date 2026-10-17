"""In-memory stores, for tests and for a server run without a data directory (DESIGN §3)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace

from opencs.store.base import Account, ProgressRecord, Stores


class MemoryAccountStore:
    """``AccountStore`` backed by a dict; forgets everything at exit."""

    def __init__(self) -> None:
        self._accounts: dict[str, Account] = {}

    def get(self, name: str) -> Account | None:
        return self._accounts.get(name)

    def create(self, account: Account) -> bool:
        if account.name in self._accounts:
            return False
        self._accounts[account.name] = account
        return True

    def callsign_registered(self, callsign: str, length: int) -> bool:
        return any(name[:length] == callsign for name in self._accounts)


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


def memory_stores() -> Stores:
    """A fresh set of in-memory stores."""
    return Stores(MemoryAccountStore(), MemoryProgressStore())
