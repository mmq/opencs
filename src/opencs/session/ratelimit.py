"""A token bucket on the session clock (DESIGN §5)."""

from __future__ import annotations


class TokenBucket:
    """Allows bursts of up to ``capacity`` events and ``refill_per_second`` sustained.

    It starts full. Time comes from the caller, so tests drive it with a ``ManualClock``.
    """

    def __init__(self, capacity: int, refill_per_second: float, now: float) -> None:
        self._capacity = float(capacity)
        self._rate = refill_per_second
        self._tokens = float(capacity)
        self._at = now

    def take(self, now: float) -> bool:
        """Spend one token at ``now``; False, spending nothing, if there is none."""
        self._tokens = min(self._capacity, self._tokens + max(0.0, now - self._at) * self._rate)
        self._at = now
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True
