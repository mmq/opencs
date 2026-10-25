"""Who a login line is: real accounts and ``JOINGENIE`` guests, and their callsigns.

II §3, II §3, I §5.1, II §3. ``Identity`` knows nothing of sockets or sessions: it
takes the account line and answers with a ``Pilot`` or a reason to refuse. It tracks who is online
so a second login is refused and callsigns stay unique server-wide.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, field
from typing import Protocol

from opencs import policy
from opencs.store.base import Account, AccountStore


def identity_key(callsign: str, account: str | None) -> str:
    """The key a player's record is stored under (II §3): ``acct:NAME`` for a
    real account, else the callsign the lobby assigned (``JOINGENI``, ``JOINGEN2``, ...)."""
    return callsign if account is None else policy.ACCOUNT_KEY_PREFIX + account


class LoginGate(Protocol):
    """Meters the costly steps of a login across connections (DESIGN §5). The session layer
    supplies one per attempt, knowing the source address; ``Identity`` knows only the two steps."""

    def may_check_password(self) -> bool:
        """Whether a password check (a PBKDF2 run) may be made now."""
        ...

    def may_register(self) -> bool:
        """Whether a new account may be created now."""
        ...

    def may_admit_guest(self) -> bool:
        """Whether another guest may log in now."""
        ...

    def may_try_account(self, name: str) -> bool:
        """Whether a password for account ``name`` may be checked now: not while its recent
        failures are over their limit, whoever made them."""
        ...

    def account_failed(self, name: str) -> None:
        """A wrong password was given for ``name``."""
        ...

    def account_succeeded(self, name: str) -> None:
        """The right password was given for ``name``: its failures are forgotten."""
        ...


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


@dataclass(frozen=True, slots=True)
class PasswordCheck:
    """An account login waiting for its password hash (DESIGN §5). ``hash`` is pure CPU and touches
    nothing shared, so it may run on another thread; its result goes back to ``Identity.complete``
    on the event loop. ``stored`` is the account to compare with, None if the login registers it."""

    name: str
    password: str = field(repr=False)
    salt: bytes = field(repr=False)
    iterations: int
    stored: Account | None

    def hash(self) -> bytes:
        return hash_password(self.password, self.salt, self.iterations)


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

    def login(self, line: str, gate: LoginGate | None = None) -> Pilot | Refused:
        """``begin`` and, for an account, ``complete`` with the hash made here and now. The
        session runs the hash off the event loop instead; this is for callers that may block."""
        result = self.begin(line, gate)
        if isinstance(result, PasswordCheck):
            return self.complete(result, result.hash(), gate)
        return result

    def begin(self, line: str, gate: LoginGate | None = None) -> Pilot | Refused | PasswordCheck:
        """Resolve an account line (I §5.1): ``NAME,PASSWORD`` or ``NAME``.

        - A configured guest name, with or without anything after it, is a guest: it gets a
          callsign from its name (II §3). ``JOINGENIE`` is refused when it is not one.
        - ``NAME,PASSWORD`` is a real account: the first login registers the password, later
          ones must match, a second simultaneous one is refused (II §3). ``NAME`` alone is
          refused for want of a password.

        ``gate`` meters the password checks and registrations; over its limit the login is refused.
        A guest is answered at once; an account login needs its password hashed first, so the
        answer is a ``PasswordCheck`` to run and hand to ``complete``.
        """
        name, _, password = line.strip().partition(",")
        name = name.strip().upper()
        if not name:
            return Refused("EMPTY LOGIN NAME")
        if len(name) > policy.MAX_ACCOUNT_NAME_LENGTH:
            return Refused("LOGIN NAME TOO LONG")
        if name in self._guest_names:
            return self._guest(name, gate)
        if name == policy.GUEST_ACCOUNT_NAME:
            return Refused("GUEST LOGINS ARE TURNED OFF ON THIS SERVER")
        return self._account(name, password, gate)

    def release(self, pilot: Pilot) -> None:
        """The pilot has gone: free its callsign and account for the next login."""
        self._callsigns.discard(pilot.callsign)
        if pilot.account is not None:
            self._accounts.discard(pilot.account)

    def callsign_online(self, callsign: str) -> bool:
        """True if ``callsign`` (upper case) is in use."""
        return callsign in self._callsigns

    def _guest(self, name: str, gate: LoginGate | None) -> Pilot | Refused:
        # Compare callsigns, not names: ALPHAXYZW would otherwise be played as ALPHAXYZ's callsign.
        base = name[: policy.MAX_CALLSIGN_LENGTH]
        if name != policy.GUEST_ACCOUNT_NAME and self._store.callsign_registered(
            base, policy.MAX_CALLSIGN_LENGTH
        ):
            return Refused("THAT NAME IS A REGISTERED ACCOUNT: LOG IN WITH ITS PASSWORD")
        if gate is not None and not gate.may_admit_guest():
            return Refused("TOO MANY GUESTS ONLINE: TRY AGAIN LATER")
        return self._admit(name, None)

    def complete(
        self, check: PasswordCheck, digest: bytes, gate: LoginGate | None = None
    ) -> Pilot | Refused:
        """Finish an account login with ``check.hash()``. The world may have moved on while it
        ran: the account may have logged in elsewhere, or been registered by another login."""
        if check.stored is None:
            account = Account(check.name, check.salt, digest, check.iterations)
            if not self._store.create(account):
                return Refused("THAT NAME WAS JUST TAKEN: TRY AGAIN")
        elif not hmac.compare_digest(check.stored.password_hash, digest):
            if gate is not None:
                gate.account_failed(check.name)
            return Refused("WRONG PASSWORD")
        if gate is not None:
            gate.account_succeeded(check.name)
        if check.name in self._accounts:
            return Refused("THAT ACCOUNT IS ALREADY LOGGED IN")
        return self._admit(check.name, check.name)

    def _account(self, name: str, password: str, gate: LoginGate | None) -> Refused | PasswordCheck:
        if (refusal := self._refuse_account(name, password, gate)) is not None:
            return refusal
        account = self._store.get(name)
        if account is not None:
            return PasswordCheck(name, password, account.salt, account.iterations, account)
        if gate is not None and not gate.may_register():
            return Refused("TOO MANY NEW ACCOUNTS: TRY AGAIN LATER")
        salt = os.urandom(policy.PBKDF2_SALT_BYTES)
        return PasswordCheck(name, password, salt, self._iterations, None)

    def _refuse_account(self, name: str, password: str, gate: LoginGate | None) -> Refused | None:
        """The cheap reasons to turn an account login away, before any hashing; the gate last,
        because it spends an allowance."""
        if not password:
            return Refused("PASSWORD REQUIRED")
        if len(password) > policy.MAX_PASSWORD_LENGTH:
            return Refused("PASSWORD TOO LONG")
        if name in self._accounts:
            return Refused("THAT ACCOUNT IS ALREADY LOGGED IN")
        if gate is not None and not gate.may_try_account(name):
            return Refused("TOO MANY WRONG PASSWORDS FOR THAT ACCOUNT: WAIT A FEW MINUTES")
        if gate is not None and not gate.may_check_password():
            return Refused("TOO MANY LOGIN ATTEMPTS: WAIT A MOMENT")
        return None

    def _admit(self, name: str, account: str | None) -> Pilot | Refused:
        callsign = self._allocate_callsign(name)
        if callsign is None:
            return Refused("TOO MANY PILOTS WITH THAT NAME ARE ONLINE")
        self._callsigns.add(callsign)
        if account is not None:
            self._accounts.add(account)
        return Pilot(callsign, account)

    def _allocate_callsign(self, name: str) -> str | None:
        """The name cut to 12 characters, or with its tail replaced by a number until free
        (II §3: ``JOINGENIE``, ``JOINGENIE2`` ... ``JOINGENIE999``)."""
        base = name[: policy.MAX_CALLSIGN_LENGTH]
        if base not in self._callsigns:
            return base
        for n in range(policy.CALLSIGN_FIRST_SUFFIX, policy.CALLSIGN_LAST_SUFFIX + 1):
            suffix = str(n)
            candidate = base[: policy.MAX_CALLSIGN_LENGTH - len(suffix)] + suffix
            if candidate not in self._callsigns:
                return candidate
        return None
