"""The GEnie logon (I §5.1), as a sans-IO phase.

::

    client HHH            server CONNECT\\r\\n  U#=
    client \\r             (a bare CR: skipped)
    client ACCOUNT\\r      server PASSWORD:     (and, 0.3 s later or on <Not Telling!>, \\r\\n*)
    client <Not Telling!>\\r
    client M 1380;3\\r     -> the Chamber

The same phase is the ``*`` prompt the Chamber's ``/QUIT`` returns to, where ``BYE`` closes and
``M 1380;3`` enters the Chamber again.
"""

from __future__ import annotations

import re
from enum import Enum, auto
from typing import TYPE_CHECKING

from opencs import policy
from opencs.lobby.identity import PasswordCheck, Pilot, Refused
from opencs.session.phases import TextPhase
from opencs.wire.text import HHHScanner, plain_line

if TYPE_CHECKING:
    from opencs.session.loginlimits import AddressGate
    from opencs.session.session import Session

CONNECT = b"CONNECT\r\n"
ACCOUNT_PROMPT = b"U#="
PASSWORD_PROMPT = b"PASSWORD: "
STAR_PROMPT = b"\r\n*"
NOT_TELLING = "<Not Telling!>"
_ENTER_CHAMBER = re.compile(r"M\s*1380\s*;\s*3", re.IGNORECASE)
BYE = "BYE"
GOODBYE = "GOODBYE"


class Stage(Enum):
    """Where the logon is."""

    HHH = auto()
    ACCOUNT = auto()
    CHECKING = auto()  # the password is being hashed; lines wait
    PROMPT = auto()  # account accepted, waiting to send the "*" prompt
    STAR = auto()  # at GEnie's "*" level


class LoginPhase(TextPhase):
    """Scans for ``HHH``, takes the account line, sends the prompts and waits for the GO."""

    def __init__(self, host: Session, *, at_prompt: bool = False) -> None:
        super().__init__(host)
        self._at_prompt = at_prompt
        self._stage = Stage.STAR if at_prompt else Stage.HHH
        self._scanner = HHHScanner()
        self._preamble = 0
        self._lines_seen = 0
        self._prompt_due: float | None = None
        self._not_telling_expected = False
        self._deadline = 0.0
        self._held = b""  # bytes that arrived while CHECKING

    def enter(self) -> None:
        now = self.host.now()
        if self._at_prompt:
            self._deadline = now + policy.GENIE_IDLE_TIMEOUT
            self.host.io.send_raw(STAR_PROMPT)
        else:
            self._deadline = now + policy.LOGIN_TIMEOUT

    # --- input -----------------------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        if self._stage is Stage.HHH:
            self._preamble += len(data)
            rest = self._scanner.feed(data)
            if rest is None:
                if self._preamble > policy.LOGIN_PREAMBLE_LIMIT:
                    self.host.close("no HHH within the preamble limit")
                return
            self._stage = Stage.ACCOUNT
            self.host.io.send_raw(CONNECT + ACCOUNT_PROMPT)
            data = rest
        super().feed(data)

    def on_overlong(self) -> None:
        self.host.close("login line too long")

    def holding(self) -> bool:
        return self._stage is Stage.CHECKING

    def hold(self, data: bytes) -> None:
        """The client does not wait for the prompts (it sends ``<Not Telling!>`` and may send
        ``M 1380;3`` at once), so what follows the account line is kept, within the preamble
        limit, and read when the check is done."""
        self._held += data
        if len(self._held) > policy.LOGIN_PREAMBLE_LIMIT:
            self.host.close("too much input during the password check")

    def on_line(self, line: str) -> None:
        self._lines_seen += 1
        if self._lines_seen > policy.LOGIN_MAX_LINES:
            self.host.close("too many lines before the Chamber")
            return
        text = line.strip()
        if self._stage is Stage.ACCOUNT:
            if text:  # the first non-empty line is the account (I §5.1)
                self._account(text)
        elif self._stage is Stage.PROMPT:
            if text == NOT_TELLING:
                self._not_telling_expected = False
                self._send_prompt()
        elif text:
            self._star_line(text)

    def _account(self, text: str) -> None:
        gate = self.host.services.login_limits.gate(self.host.address)
        result = self.host.services.identity.begin(text, gate)
        if isinstance(result, PasswordCheck):
            self._stage = Stage.CHECKING
            check = result

            def done(digest: bytes) -> None:
                self.host.call(self._checked, check, digest, gate)

            self.host.services.hasher.submit(check.hash, done)
            return
        self._answer(result)

    def _checked(self, check: PasswordCheck, digest: bytes, gate: AddressGate) -> None:
        """The password check is done: answer, then read what arrived meanwhile."""
        if self.host.phase is not self or self._stage is not Stage.CHECKING:
            return
        self._answer(self.host.services.identity.complete(check, digest, gate))
        held, self._held = self._held, b""
        if held:
            self.feed(held)

    def _answer(self, result: Pilot | Refused) -> None:
        if isinstance(result, Refused):
            self.host.log.info("login refused from %s: %s", self.host.address, result.reason)
            self.host.io.send_raw(plain_line(result.reason))
            self.host.close("login refused")
            return
        self.host.set_pilot(result)
        self.host.io.send_raw(PASSWORD_PROMPT)
        self._stage = Stage.PROMPT
        self._prompt_due = self.host.now() + policy.LOGIN_PROMPT_DELAY
        self._not_telling_expected = True

    def _send_prompt(self) -> None:
        """Send ``\\r\\n*`` once: on ``<Not Telling!>``, or when its delay passes first."""
        self.host.io.send_raw(STAR_PROMPT)
        self._stage = Stage.STAR
        self._prompt_due = None
        self._deadline = self.host.now() + policy.GENIE_IDLE_TIMEOUT

    def _star_line(self, text: str) -> None:
        self._deadline = self.host.now() + policy.GENIE_IDLE_TIMEOUT
        if text == NOT_TELLING and self._not_telling_expected:
            self._not_telling_expected = False  # it arrived after the timer sent "*": ignore it
        elif _ENTER_CHAMBER.fullmatch(text):
            self.host.to_chamber()
        elif text.upper() == BYE:
            self.host.io.send_raw(plain_line(GOODBYE))
            self.host.close("BYE")
        else:
            self.host.io.send_raw(STAR_PROMPT)

    # --- time ------------------------------------------------------------------------------

    def tick(self, now: float) -> None:
        if self._prompt_due is not None and now >= self._prompt_due:
            self._send_prompt()
        if now >= self._deadline:
            self.host.close("login timed out")
