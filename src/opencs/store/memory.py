"""In-memory stores, for tests and for a server run without a data directory (DESIGN §3)."""

from __future__ import annotations

from opencs.store.base import Account


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
