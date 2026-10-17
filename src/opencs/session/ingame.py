"""The in-game phase: a placeholder until M4 (I §5.5, I §4.1).

It reads binary frames, decodes them, counts what is bad and ignores the rest. The real session
(the ``'B'``, the drop, the ping, the quit handshake) replaces its ``handle`` in M4.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opencs import policy
from opencs.messages import DecodeError, decode_client
from opencs.session.badinput import WindowedCounter
from opencs.session.phases import Phase
from opencs.wire.frame import Frame, FrameReader

if TYPE_CHECKING:
    from opencs.session.session import Session


class InGamePhase(Phase):
    """Binary frames in, nothing out yet."""

    def __init__(self, host: Session) -> None:
        super().__init__(host)
        self._reader = FrameReader()
        self._bad = WindowedCounter(policy.MAX_BAD_INPUT, policy.BAD_INPUT_WINDOW)
        self._bad_seen = 0
        self._stalled_since: float | None = None

    def enter(self) -> None:
        pass

    def feed(self, data: bytes) -> None:
        now = self.host.now()
        frames = self._reader.feed(data)
        self._process(frames, now)
        # A partial frame that is not making progress starts the stall clock (see ``tick``).
        if self._reader.pending == 0 or frames:
            self._stalled_since = None
        elif self._stalled_since is None:
            self._stalled_since = now

    def tick(self, now: float) -> None:
        """Give up on a header whose frame never completed, so real frames inside its claimed
        span are not held back for ever (our partial-frame timeout)."""
        since = self._stalled_since
        if since is not None and now - since >= policy.FRAME_STALL_SECONDS:
            self.host.log.debug(
                "abandoning a stalled partial frame (%d bytes)", self._reader.pending
            )
            self._process(self._reader.give_up_pending(), now)
            self._stalled_since = now if self._reader.pending else None

    def _process(self, frames: list[Frame], now: float) -> None:
        failed = self._reader.bad_frames - self._bad_seen
        self._bad_seen = self._reader.bad_frames
        for frame in frames:
            try:
                message = decode_client(frame.payload)
            except DecodeError as exc:
                failed += 1
                self.host.log.debug("undecodable %r frame: %s", frame.type, exc)
                continue
            self.handle(message)
        if self._bad.record(now, failed):
            self.host.log.warning(
                "%d bad frames or messages in %.0f s; closing",
                self._bad.total,
                policy.BAD_INPUT_WINDOW,
            )
            self.host.close("too much bad input")

    def handle(self, message: object) -> None:
        """M4 gives this a body."""
