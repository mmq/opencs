"""Server-wide limits on password checks and new accounts (DESIGN §5 "login attempts").

PBKDF2 runs on the event loop, so a flood of logins, from one address or many, would stall every
city. Each costly step spends a token from the source address's bucket and from a global one; the
numbers are in ``policy``. The gate handed to ``Identity`` is made per attempt.
"""

from __future__ import annotations

from collections import Counter

from opencs import policy
from opencs.clock import Clock
from opencs.ratelimit import KeyedBuckets, TokenBucket


class LoginLimits:
    """The buckets, shared by every session."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        now = clock.now()
        self._attempts = KeyedBuckets(*policy.LOGIN_ATTEMPTS_PER_ADDRESS)
        self._attempts_global = TokenBucket(*policy.LOGIN_ATTEMPTS_GLOBAL, now)
        self._accounts = KeyedBuckets(*policy.NEW_ACCOUNTS_PER_ADDRESS)
        self._accounts_global = TokenBucket(*policy.NEW_ACCOUNTS_GLOBAL, now)
        self._guests: Counter[str] = Counter()  # guests online, by address

    def may_admit_guest(self, address: str) -> bool:
        """Whether a guest from ``address`` may log in now (``policy.MAX_GUESTS*``)."""
        return (
            self._guests[address] < policy.MAX_GUESTS_PER_ADDRESS
            and self._guests.total() < policy.MAX_GUESTS
        )

    def guest_in(self, address: str) -> None:
        """A guest from ``address`` has logged in."""
        self._guests[address] += 1

    def guest_out(self, address: str) -> None:
        """A guest from ``address`` has gone."""
        self._guests[address] -= 1
        if self._guests[address] <= 0:
            del self._guests[address]

    def gate(self, address: str) -> AddressGate:
        return AddressGate(self, address)

    def attempt(self, address: str) -> bool:
        """Spend one password check for ``address``. The address is charged first, so a flooder
        cannot use up the global allowance for everyone else once it is itself refused."""
        now = self._clock.now()
        return self._attempts.take(address, now) and self._attempts_global.take(now)

    def register(self, address: str) -> bool:
        """Spend one new account for ``address``."""
        now = self._clock.now()
        return self._accounts.take(address, now) and self._accounts_global.take(now)


class AddressGate:
    """A ``LoginGate`` for one source address."""

    def __init__(self, limits: LoginLimits, address: str) -> None:
        self._limits = limits
        self._address = address

    def may_check_password(self) -> bool:
        return self._limits.attempt(self._address)

    def may_register(self) -> bool:
        return self._limits.register(self._address)

    def may_admit_guest(self) -> bool:
        return self._limits.may_admit_guest(self._address)
