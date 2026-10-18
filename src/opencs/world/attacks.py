"""When a pod was last under attack, for ``/RESUME``'s "a fight you are about to lose" (II §13 item
3, D4).

Only what the server is told counts: a non-teammate's hit report naming the pod, an ``'I'``
delivered to it, and an enemy shot or grenade fired within ``FIGHT_RANGE`` of it (an Air Support
strike is read from ``Orders.strikes`` at the drop). Power is not used: damage inferred from it
confuses jumps and crashes with hits, and a full-power pod can still be losing. A pod that was
attacked at all within ``BATTLE_WINDOW`` of a drop was in a fight.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from opencs import policy
from opencs.messages import WirePos


@dataclass(slots=True)
class Attacks:
    """When one pod was attacked, and how: a short history, so a drop can be judged as of the
    moment the line went quiet and not of the moment it was noticed (II §13 item 3, D8)."""

    _marks: list[tuple[float, str]] = field(default_factory=list)
    #: When the pod's current life began (its last death); earlier attacks belong to a life whose
    #: death has been paid and are not a fight it is running from.
    life_started: float | None = None

    def mark(self, now: float, why: str) -> None:
        """The pod was attacked at ``now``."""
        self._marks.append((now, why))
        # Keep what a silent line can still ask for: the fight window before the last byte, which
        # is up to the silence limit before the time it is noticed.
        keep = now - policy.ATTACK_HISTORY
        if self._marks[0][0] < keep:
            self._marks = [m for m in self._marks if m[0] >= keep]

    def new_life(self, now: float) -> None:
        """The pod died at ``now``: what came before it no longer counts."""
        self._marks.clear()
        self.life_started = now

    def between(self, lo: float, hi: float) -> str:
        """How the pod was last attacked within ``[lo, hi]`` (and in its current life), else an
        empty string."""
        if self.life_started is not None:
            lo = max(lo, self.life_started)
        for at, why in reversed(self._marks):
            if at <= hi:
                return why if at >= lo else ""
        return ""

    def since(self, t: float) -> str:
        """How the pod was last attacked if that was at or after ``t``, else an empty string."""
        return self.between(t, math.inf)


def in_fight_range(a: WirePos, b: WirePos) -> bool:
    """True if two points are within ``FIGHT_RANGE`` feet, straight-line across the ground (D4)."""
    return math.hypot(a.world_x - b.world_x, a.world_z - b.world_z) <= policy.FIGHT_RANGE
