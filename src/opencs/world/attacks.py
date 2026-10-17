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
from dataclasses import dataclass

from opencs import policy
from opencs.messages import WirePos


@dataclass(slots=True)
class Attacks:
    """The last time one pod was attacked, and how."""

    at: float | None = None
    why: str = ""

    def mark(self, now: float, why: str) -> None:
        """The pod was attacked at ``now``."""
        self.at, self.why = now, why

    def since(self, t: float) -> str:
        """How the pod was last attacked if that was at or after ``t``, else an empty string."""
        return self.why if self.at is not None and self.at >= t else ""


def in_fight_range(a: WirePos, b: WirePos) -> bool:
    """True if two points are within ``FIGHT_RANGE`` feet, straight-line across the ground (D4)."""
    return math.hypot(a.world_x - b.world_x, a.world_z - b.world_z) <= policy.FIGHT_RANGE
