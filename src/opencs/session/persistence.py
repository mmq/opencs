"""Getting records between the world and the store (II §11.3, DESIGN §3, ADR 2).

The world cannot do I/O, so a city only *says* which records changed (``City.take_progress``) and
takes the stored one as a value at join. This is the other half: it loads a record before a join,
and after every call into a city it writes what the city reported. Writes run on the event loop
and are small, single-row transactions.

A write that fails is logged and retried with the next flush, so a database that blips does not
cost a record; the retry always writes the *newest* record of that identity, never an older one.
After a failure nothing is tried again for ``SAVE_RETRY_INTERVAL`` (a failing save can block for the
store's busy timeout, and a flush follows every client message); a shutdown save ignores the wait.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from opencs import policy
from opencs.clock import Clock
from opencs.store.base import ProgressRecord, ProgressStore
from opencs.world.city import City
from opencs.world.progress import ProgressChanged, SavedProgress

log = logging.getLogger("opencs.session")


def to_saved(record: ProgressRecord) -> SavedProgress:
    """A stored record as the world takes it."""
    return SavedProgress(record.bays, record.marker, record.bay_points, record.score, record.career)


def to_record(saved: SavedProgress) -> ProgressRecord:
    """A world record as the store keeps it (the store stamps ``updated``)."""
    return ProgressRecord(saved.bays, saved.marker, saved.bay_points, saved.score, saved.career)


class Persistence:
    """Loads records for the world and writes the ones it reports."""

    def __init__(self, store: ProgressStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock
        self._hold_until = 0.0  # no save is tried before this time
        self._retry: dict[str, SavedProgress] = {}

    def record(self, key: str) -> ProgressRecord | None:
        """The stored record for ``key`` (``/SCORE``, ``/WHO``). A record whose write is waiting
        for a retry is not in the store yet, so it is answered from the waiting copy."""
        waiting = self._retry.get(key)
        if waiting is not None:
            return to_record(waiting)
        return self._store.load(key)

    def load(self, key: str) -> SavedProgress | None:
        """The record a joining player starts from, None for a new identity. Raises if the store
        cannot be read: the caller must then keep nothing for that player, not overwrite it."""
        stored = self.record(key)
        return None if stored is None else to_saved(stored)

    def erase(self, key: str) -> bool:
        """Remove the stored record of ``key``, and any write of it still waiting (the operator's
        ``reset-progress``, ``delete-account``). The player must not be in a game, or its next
        report would write the record again."""
        waiting = self._retry.pop(key, None) is not None
        return self._store.erase(key) or waiting

    def flush(self, city: City) -> None:
        """Write what ``city`` reports as changed, after the waiting retries."""
        self._write(city.take_progress())

    def save_all(self, cities: Iterable[City]) -> None:
        """Write every kept player's record in these cities (a graceful shutdown, M12)."""
        for city in cities:
            self._write(city.all_progress(), force=True)

    def _write(self, changes: Iterable[ProgressChanged], *, force: bool = False) -> None:
        pending = dict(self._retry)
        pending.update({n.identity: n.record for n in changes})  # the newest wins
        if not force and self._clock.now() < self._hold_until:
            self._retry = pending
            return
        self._retry = {}
        items = list(pending.items())
        for n, (identity, saved) in enumerate(items):
            try:
                self._store.save(identity, to_record(saved))
            except Exception:
                log.exception("could not save the record of %s; will retry", identity)
                self._hold_until = self._clock.now() + policy.SAVE_RETRY_INTERVAL
                if force:
                    self._retry[identity] = saved
                    continue
                # The store is failing: do not spend a timeout on every other record as well.
                self._retry.update(items[n:])
                return
