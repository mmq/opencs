"""Counting bad input over a sliding window (DESIGN §5)."""

from __future__ import annotations

from collections import deque


class WindowedCounter:
    """Counts events in the last ``window`` seconds and says when they exceed ``limit``.

    Real clients send some undecodable frames (I §4.1), so a lifetime total would close honest
    connections on a long session; a hostile flood still trips this within one window.
    """

    def __init__(self, limit: int, window: float) -> None:
        self._limit = limit
        self._window = window
        self._events: deque[tuple[float, int]] = deque()
        self._total = 0

    def record(self, now: float, n: int = 1) -> bool:
        """Add ``n`` events at ``now``; True if the window now holds more than the limit."""
        if n > 0:
            self._events.append((now, n))
            self._total += n
        while self._events and self._events[0][0] <= now - self._window:
            self._total -= self._events.popleft()[1]
        return self._total > self._limit

    @property
    def total(self) -> int:
        """Events currently inside the window."""
        return self._total
