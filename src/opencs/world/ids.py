"""Entity ids and when they may be reused (I §6.2, II §8).

A client never unlinks a registry entry, so an id must not be reused while any connected client may
still hold it. A client that started after the id was released has a fresh registry and is safe,
which is what lets a city that never ends (S13) reuse ids at all. Each range hands out fresh ids in
order; only when the range is exhausted does it reuse, earliest-released first, and only an id every
connected client started after.
"""

from __future__ import annotations

from collections import deque
from enum import Enum

from opencs import policy


class IdRange(Enum):
    """The ranges of II §8; the value is ``(low, high)``, inclusive."""

    PLAYER = policy.PLAYER_IDS
    RIVAL_POD = policy.RIVAL_POD_IDS
    GUPPY = policy.GUPPY_IDS
    MUNCHER = policy.MUNCHER_IDS
    SENTINEL = policy.SENTINEL_IDS
    STRUCTURE = policy.STRUCTURE_IDS


class IdAllocator:
    """Ids of one range."""

    def __init__(self, low: int, high: int) -> None:
        self._next = low
        self._high = high
        # (id, release time), in release order.
        self._released: deque[tuple[int, float]] = deque()

    def allocate(self, earliest_start: float | None) -> int | None:
        """The next id, or None if the range is exhausted and nothing is safely reusable.

        ``earliest_start`` is the earliest join time among connected clients (None when nobody is
        connected). The earliest-released id is reused only if that client started strictly after
        its release: a client that joined at the very moment of release may hold it.
        """
        if self._next <= self._high:
            fresh = self._next
            self._next += 1
            return fresh
        if self._released and (earliest_start is None or earliest_start > self._released[0][1]):
            return self._released.popleft()[0]
        return None

    def release(self, entity_id: int, now: float) -> None:
        """Note that ``entity_id`` is gone as of ``now``."""
        self._released.append((entity_id, now))


class IdSpace:
    """One allocator per range."""

    def __init__(self) -> None:
        self._allocators = {r: IdAllocator(*r.value) for r in IdRange}

    def allocate(self, which: IdRange, earliest_start: float | None) -> int | None:
        return self._allocators[which].allocate(earliest_start)

    def release(self, which: IdRange, entity_id: int, now: float) -> None:
        self._allocators[which].release(entity_id, now)
