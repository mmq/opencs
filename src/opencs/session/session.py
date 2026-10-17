"""One connection's session: the phase machine (I §7.8, DESIGN §3.4).

Sans-IO: it is fed bytes and ticks, and writes through a ``SessionIO``. No exception escapes
``feed``, ``tick`` or ``closed`` (DESIGN §5): a failure is logged with its context and the
connection is closed.
"""

from __future__ import annotations

from collections.abc import Callable

from opencs.connlog import ConnectionLog
from opencs.lobby.identity import Pilot
from opencs.lobby.lobby import Member
from opencs.session.chamber import ChamberPhase
from opencs.session.ingame import InGamePhase
from opencs.session.io import SessionIO
from opencs.session.login import LoginPhase
from opencs.session.phases import Phase, Services


class Session:
    """The phases of one connection and what they share: the pilot and its lobby member."""

    def __init__(self, io: SessionIO, services: Services, log: ConnectionLog) -> None:
        self.io = io
        self.services = services
        self.log = log
        self.pilot: Pilot | None = None
        self.member: Member | None = None
        self._active = True
        self._released = False
        self.phase: Phase = LoginPhase(self)
        self._guard(self.phase.enter)

    # --- the connection's side ---------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        """Bytes from the socket."""
        if self._active:
            self._guard(self.phase.feed, data)

    def tick(self, now: float) -> None:
        """Time has passed (``now`` is on the session clock)."""
        if self._active:
            self._guard(self.phase.tick, now)

    def closed(self) -> None:
        """The socket has ended: free the callsign and leave the lobby. Safe to call twice."""
        self._active = False
        if not self._released:
            self._released = True
            self._guard(self.phase.closed)
            self.leave_lobby()
            if self.pilot is not None:
                self.services.identity.release(self.pilot)
                self.log.info("logged out")

    # --- for the phases ----------------------------------------------------------------------

    @property
    def active(self) -> bool:
        """False once the session has asked to close (or the socket ended)."""
        return self._active

    def now(self) -> float:
        return self.services.clock.now()

    def close(self, reason: str) -> None:
        """Ask the connection to flush and close; the session stops reading."""
        if self._active:
            self._active = False
            self.log.info("closing: %s", reason)
            self.io.close(reason)

    def set_pilot(self, pilot: Pilot) -> None:
        self.pilot = pilot
        self.log.callsign = pilot.callsign
        self.log.info(
            "logged in as %s (%s)", pilot.callsign, "guest" if pilot.is_guest else "account"
        )

    def join_lobby(self) -> Member:
        """Enter the Chamber's roster (the pilot must be known)."""
        assert self.pilot is not None
        self.leave_lobby()
        self.member = self.services.lobby.join(
            self.pilot.callsign, self.pilot.account, self.io.send_raw
        )
        return self.member

    def leave_lobby(self) -> None:
        if self.member is not None:
            self.services.lobby.leave(self.member)
            self.member = None

    def to_chamber(self) -> None:
        self._switch(ChamberPhase(self))

    def return_to_chamber(self) -> None:
        """Back from a game on the same connection: the member is in the Chamber again and the
        buttons come back (I §5.5 step 5)."""
        assert self.member is not None
        self.services.lobby.return_to_chamber(self.member)
        self._switch(ChamberPhase(self, returning=True))

    def to_login_prompt(self) -> None:
        self._switch(LoginPhase(self, at_prompt=True))

    def to_ingame(self) -> None:
        self._switch(InGamePhase(self))

    def hand_over(self, data: bytes) -> None:
        """Give bytes the previous phase did not consume to the current one."""
        if data and self._active:
            self.phase.feed(data)

    # --- internals ---------------------------------------------------------------------------

    def _switch(self, phase: Phase) -> None:
        self.log.info("phase %s -> %s", type(self.phase).__name__, type(phase).__name__)
        self.phase = phase
        phase.enter()

    def _guard[**P](self, fn: Callable[P, None], *args: P.args, **kwargs: P.kwargs) -> None:
        """Run a handler so that nothing it raises escapes (DESIGN §5)."""
        try:
            fn(*args, **kwargs)
        except Exception:
            self.log.exception(
                "unhandled error in %s; closing the connection", type(self.phase).__name__
            )
            if self._active:
                self._active = False
                self.io.close("internal error")
