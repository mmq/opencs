"""Game time. The only module that may import ``time`` for game time (DESIGN §3.4).

Rules take a ``Clock`` and read ``now()``; tests pass a ``ManualClock`` and advance it, so
nothing in the suite sleeps.
"""

import time
from typing import Protocol


class Clock(Protocol):
    """A source of monotonic seconds."""

    def now(self) -> float:
        """Seconds on a monotonic scale; only differences are meaningful."""
        ...


class MonotonicClock:
    """The real clock, backed by ``time.monotonic``."""

    def now(self) -> float:
        return time.monotonic()


class ManualClock:
    """A clock that moves only when told to; time never runs backward."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        """Move forward by ``seconds`` (zero is allowed, negative raises)."""
        if seconds < 0:
            raise ValueError(f"cannot advance a clock backward ({seconds})")
        self._now += seconds

    def set(self, t: float) -> None:
        """Jump to ``t``, which must not be earlier than now."""
        if t < self._now:
            raise ValueError(f"cannot set a clock backward ({t} < {self._now})")
        self._now = t
