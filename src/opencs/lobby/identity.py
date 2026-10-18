"""Who a login line is: real accounts and ``JOINGENIE`` guests, and their callsigns.

II §3, II §3, I §5.1, II §3. ``Identity`` knows nothing of sockets or sessions: it
takes the account line and answers with a ``Pilot`` or a reason to refuse. It tracks who is online
so a second login is refused and callsigns stay unique server-wide.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

from opencs import policy
from opencs.store.base import Account, AccountStore


def identity_key(callsign: str, account: str | None) -> str:
    """The key a player's record is stored under (II §3): ``acct:NAME`` for a
    real account, else the callsign the lobby assigned (``JOINGENI``, ``JOINGEN2``, ...)."""
    return callsign if account is None else policy.ACCOUNT_KEY_PREFIX + account


@dataclass(frozen=True, slots=True)
class Pilot:
    """A logged-in player. ``account`` is the upper-cased account name, None for a guest, whose
    persistence key is the callsign (II §3)."""

    callsign: str
    account: str | None

    @property
    def is_guest(self) -> bool:
        return self.account is None

    @property
    def key(self) -> str:
        """Where this pilot's progress is stored."""
        return identity_key(self.callsign, self.account)


@dataclass(frozen=True, slots=True)
class Refused:
    """A login that is not accepted, with a line to show the player."""

    reason: str


def hash_password(password: str, salt: bytes, iterations: int) -> bytes:
    """Salted PBKDF2-HMAC-SHA256 of ``password`` (DESIGN §5)."""
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


class Identity:
    """Logs players in and out and hands out callsigns."""

    def __init__(
        self,
        store: AccountStore,
        *,
        guest_names: tuple[str, ...] = policy.DEFAULT_GUEST_NAMES,
        iterations: int = policy.PBKDF2_ITERATIONS,
    ) -> None:
        self._store = store
        self._guest_names = frozenset(name.upper() for name in guest_names)
        self._iterations = iterations
        self._callsigns: set[str] = set()
        self._accounts: set[str] = set()

    def login(self, line: str) -> Pilot | Refused:
        """Resolve an account line (I §5.1): ``NAME,PASSWORD`` or ``NAME``.

        - A configured guest name, with or without anything after it, is a guest: it gets a
          callsign from its name (II §3). ``JOINGENIE`` is refused when it is not one.
        - ``NAME,PASSWORD`` is a real account: the first login registers the password, later
          ones must match, a second simultaneous one is refused (II §3). ``NAME`` alone is
          refused for want of a password.
        """
        name, _, password = line.strip().partition(",")
        name = name.strip().upper()
        if not name:
            return Refused("EMPTY LOGIN NAME")
        if len(name) > policy.MAX_ACCOUNT_NAME_LENGTH:
            return Refused("LOGIN NAME TOO LONG")
        if name in self._guest_names:
            return self._guest(name)
        if name == policy.GUEST_ACCOUNT_NAME:
            return Refused("GUEST LOGINS ARE TURNED OFF ON THIS SERVER")
        return self._account(name, password)

    def release(self, pilot: Pilot) -> None:
        """The pilot has gone: free its callsign and account for the next login."""
        self._callsigns.discard(pilot.callsign)
        if pilot.account is not None:
            self._accounts.discard(pilot.account)

    def callsign_online(self, callsign: str) -> bool:
        """True if ``callsign`` (upper case) is in use."""
        return callsign in self._callsigns

    def _guest(self, name: str) -> Pilot | Refused:
        # Compare callsigns, not names: ALPHAXYZW would otherwise be played as ALPHAXYZ's callsign.
        base = name[: policy.MAX_CALLSIGN_LENGTH]
        if name != policy.GUEST_ACCOUNT_NAME and self._store.callsign_registered(
            base, policy.MAX_CALLSIGN_LENGTH
        ):
            return Refused("THAT NAME IS A REGISTERED ACCOUNT: LOG IN WITH ITS PASSWORD")
        return self._admit(name, None)

    def _account(self, name: str, password: str) -> Pilot | Refused:
        if not password:
            return Refused("PASSWORD REQUIRED")
        if len(password) > policy.MAX_PASSWORD_LENGTH:
            return Refused("PASSWORD TOO LONG")
        if name in self._accounts:
            return Refused("THAT ACCOUNT IS ALREADY LOGGED IN")
        account = self._store.get(name)
        if account is None:
            salt = os.urandom(policy.PBKDF2_SALT_BYTES)
            account = Account(
                name, salt, hash_password(password, salt, self._iterations), self._iterations
            )
            if not self._store.create(account):  # lost a race: fall back to checking it
                account = self._store.get(name)
        if account is None or not hmac.compare_digest(
            account.password_hash, hash_password(password, account.salt, account.iterations)
        ):
            return Refused("WRONG PASSWORD")
        return self._admit(name, name)

    def _admit(self, name: str, account: str | None) -> Pilot | Refused:
        callsign = self._allocate_callsign(name)
        if callsign is None:
            return Refused("TOO MANY PILOTS WITH THAT NAME ARE ONLINE")
        self._callsigns.add(callsign)
        if account is not None:
            self._accounts.add(account)
        return Pilot(callsign, account)

    def _allocate_callsign(self, name: str) -> str | None:
        """The name cut to 8 characters, or with its tail replaced by a digit suffix until free
        (II §3: ``JOINGENI``, ``JOINGEN2``, ``JOINGEN3`` ...)."""
        base = name[: policy.MAX_CALLSIGN_LENGTH]
        if base not in self._callsigns:
            return base
        for n in range(policy.CALLSIGN_FIRST_SUFFIX, policy.CALLSIGN_LAST_SUFFIX + 1):
            suffix = str(n)
            candidate = base[: policy.MAX_CALLSIGN_LENGTH - len(suffix)] + suffix
            if candidate not in self._callsigns:
                return candidate
        return None
