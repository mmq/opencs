"""Server-wide limits on password checks and new accounts (DESIGN §5 "login attempts").

PBKDF2 runs on the event loop, so a flood of logins, from one address or many, would stall every
city. Each costly step spends a token from the source address's bucket and from a global one; the
numbers are in ``policy``. The gate handed to ``Identity`` is made per attempt.
"""

from __future__ import annotations

from opencs import policy
from opencs.clock import Clock
from opencs.session.ratelimit import KeyedBuckets, TokenBucket


class LoginLimits:
    """The buckets, shared by every session."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        now = clock.now()
        self._attempts = KeyedBuckets(*policy.LOGIN_ATTEMPTS_PER_ADDRESS)
        self._attempts_global = TokenBucket(*policy.LOGIN_ATTEMPTS_GLOBAL, now)
        self._accounts = KeyedBuckets(*policy.NEW_ACCOUNTS_PER_ADDRESS)
        self._accounts_global = TokenBucket(*policy.NEW_ACCOUNTS_GLOBAL, now)

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
