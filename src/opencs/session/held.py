"""The games held for players whose line dropped (II §13 items 2 and 4, D3, D8, D9).

One record per identity, kept in memory (a restart loses every held game). A record is usable for
``HOLD_SECONDS`` from the drop; once its player is back in the Chamber and has been told, it is
usable for ``TYPE_SECONDS`` from then. Expiry is read from the clock whenever a record is asked
for, so nothing has to tick. The same object is what the lobby and the cities ask for the slots
that are reserved while a game is held.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from opencs import policy
from opencs.clock import Clock
from opencs.world.resume import ResumeRecord


@dataclass(slots=True)
class _Held:
    record: ResumeRecord
    #: When the player was told it is held (back in the Chamber): starts the typing window.
    told_at: float | None = None
    #: The source address of the connection that dropped (see ``policy.MAX_HELD_PER_ADDRESS``).
    address: str = "?"

    def expires_at(self) -> float:
        if self.told_at is not None:
            return self.told_at + policy.TYPE_SECONDS
        return self.record.dropped_at + policy.HOLD_SECONDS


class HeldGames:
    """Every held record, by identity key."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._held: dict[str, _Held] = {}

    def hold(self, record: ResumeRecord, address: str = "?") -> None:
        """Keep ``record``; a newer drop of the same identity replaces the older."""
        self._held[record.identity] = _Held(record, address=address)

    def has_room(self, address: str, identity: str) -> bool:
        """True if a drop of ``identity`` from ``address`` may hold a slot: the address has fewer
        than ``MAX_HELD_PER_ADDRESS`` other live slots (the identity's own is replaced, not added).
        Only a record that can be resumed holds a seat, so only those count."""
        self._purge()
        others = sum(
            1
            for key, h in self._held.items()
            if h.address == address and h.record.eligible and key != identity
        )
        return others < policy.MAX_HELD_PER_ADDRESS

    def get(self, identity: str) -> ResumeRecord | None:
        """The live record of ``identity``, None if there is none or it has run out."""
        held = self._live(identity)
        return None if held is None else held.record

    def entered_chamber(self, identity: str) -> ResumeRecord | None:
        """The player is back in the Chamber: its record, with the typing window started (once)."""
        held = self._live(identity)
        if held is None:
            return None
        if held.told_at is None:
            held.told_at = self._clock.now()
        return held.record

    def seconds_left(self, identity: str) -> float:
        """How long the live record of ``identity`` can still be used; 0 if there is none."""
        held = self._live(identity)
        return 0.0 if held is None else max(0.0, held.expires_at() - self._clock.now())

    def live(self) -> list[tuple[ResumeRecord, float]]:
        """Every live record with the seconds it can still be used, oldest drop first (status)."""
        self._purge()
        now = self._clock.now()
        found = [(h.record, max(0.0, h.expires_at() - now)) for h in self._held.values()]
        return sorted(found, key=lambda pair: pair[0].dropped_at)

    def discard(self, identity: str) -> ResumeRecord | None:
        """Give the record up (``/PLAY``, a refused or finished resume)."""
        held = self._held.pop(identity, None)
        return None if held is None else held.record

    def slots(self, letter: str) -> Sequence[tuple[str, int]]:
        """``(identity key, team)`` of the slots reserved in the city with this letter: the live
        records that can still be resumed. A record that cannot is no reason to keep a seat."""
        self._purge()
        return [
            (key, h.record.team)
            for key, h in self._held.items()
            if h.record.city == letter and h.record.eligible
        ]

    def _live(self, identity: str) -> _Held | None:
        self._purge()
        return self._held.get(identity)

    def _purge(self) -> None:
        now = self._clock.now()
        for key in [k for k, h in self._held.items() if now >= h.expires_at()]:
            del self._held[key]
