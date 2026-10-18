"""Entity ids and when they may be reused (I §6.2, II §8).

A client never unlinks a registry entry, so an id must not be reused while any connected client may
still hold it. A client that started after the id was released has a fresh registry and is safe,
which is what lets a city that never ends (S13) reuse ids at all. Each range hands out fresh ids in
order; only when the range is exhausted does it reuse, earliest-released first, and only an id every
connected client started after.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from enum import Enum
from itertools import islice

from opencs import policy


class IdRange(Enum):
    """The ranges of II §8; the value is ``(low, high)``, inclusive."""

    PLAYER = policy.PLAYER_IDS
    GUPPY = policy.GUPPY_IDS
    MUNCHER = policy.MUNCHER_IDS
    SENTINEL = policy.SENTINEL_IDS
    STRUCTURE = policy.STRUCTURE_IDS


def is_allocatable(entity_id: int) -> bool:
    """Whether the server ever hands out this id: any other cannot be reused, so there is nothing
    to guard (and a hostile client's arbitrary ids need not be remembered)."""
    return any(r.value[0] <= entity_id <= r.value[1] for r in IdRange)


class IdAllocator:
    """Ids of one range."""

    def __init__(self, low: int, high: int) -> None:
        self._low = low
        self._next = low
        self._high = high
        # (id, release time), in release order.
        self._released: deque[tuple[int, float]] = deque()
        self._waiting: set[int] = set()  # the ids in ``_released``

    def allocate(
        self, earliest_start: float | None, holds: Callable[[int], bool] | None = None
    ) -> int | None:
        """The next id, or None if the range is exhausted and nothing is safely reusable.

        ``earliest_start`` is the earliest join time among connected clients (None when nobody is
        connected). A released id is safe if that client started strictly after its release: a
        client that joined at the very moment of release may hold it. ``holds(id)`` says whether
        any connected client has ever been sent a create for the id, which is the exact question
        (I §6.2: only a client that saw the old entity is at risk); an id nobody holds is safe
        however long the connected clients have been here, so a client that stays for hours does
        not use up the range. The earliest-released safe id among the first
        ``policy.ID_REUSE_SCAN`` is the one reused.
        """
        if self._next <= self._high:
            fresh = self._next
            self._next += 1
            return fresh
        for index, (entity_id, released_at) in enumerate(
            islice(self._released, policy.ID_REUSE_SCAN)
        ):
            if (
                earliest_start is None
                or earliest_start > released_at
                or (holds is not None and not holds(entity_id))
            ):
                del self._released[index]
                self._waiting.discard(entity_id)
                return entity_id
        return None

    def release(self, entity_id: int, now: float) -> None:
        """Note that ``entity_id`` is gone as of ``now``."""
        if entity_id in self._waiting or not self._low <= entity_id < self._next:
            return  # released twice, or never handed out: no duplicate may be handed out
        self._waiting.add(entity_id)
        self._released.append((entity_id, now))


class IdSpace:
    """One allocator per range. ``ranges`` replaces the I §6.2, II §8 ranges (a test makes them
    small so that reuse happens within a short run)."""

    def __init__(self, ranges: Mapping[IdRange, tuple[int, int]] | None = None) -> None:
        self._allocators = {r: IdAllocator(*(ranges or {}).get(r, r.value)) for r in IdRange}

    def allocate(
        self,
        which: IdRange,
        earliest_start: float | None,
        holds: Callable[[int], bool] | None = None,
    ) -> int | None:
        return self._allocators[which].allocate(earliest_start, holds)

    def release(self, which: IdRange, entity_id: int, now: float) -> None:
        self._allocators[which].release(entity_id, now)
