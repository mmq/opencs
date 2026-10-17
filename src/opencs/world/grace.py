"""A pod's spawn grace period, mirrored from the client (I §9.1.5).

The client suppresses all damage to a pod while its grace lasts, including damage the server relays
(I §9.1.5), so when damage seems not to work this is the first thing to check. Grace is armed on
every spawn and respawn and ends at whichever comes first: the pod strictly outside its team's home
box, or two minutes. The server only mirrors it, to log the transition; it changes no rule yet
(``/RESUME`` eligibility and anti-cheat logging, later milestones, may read it).
"""

from __future__ import annotations

import logging

from opencs import policy
from opencs.messages import WirePos
from opencs.wire.codec import CELL_SIZE

log = logging.getLogger("opencs.world.grace")


def home_box(team: int) -> tuple[int, int, int, int]:
    """``(x_low, z_low, x_high, z_high)`` in world feet: the 3x3 tiles centered on the team's corner
    tile (I §9.1.5: ``corner x 600 - 600`` to that ``+ 1800``). Not clipped to the city: the
    test is "strictly outside", and no pod is beyond the clip but outside the box."""
    cx, cz = policy.TEAM_CORNER_CELLS[team]
    x_low = (cx + policy.GRACE_BOX_START_TILES) * CELL_SIZE
    z_low = (cz + policy.GRACE_BOX_START_TILES) * CELL_SIZE
    span = policy.GRACE_BOX_TILES * CELL_SIZE
    return x_low, z_low, x_low + span, z_low + span


class Grace:
    """One pod's grace: armed, and for how long."""

    def __init__(self, pod_id: int, team: int) -> None:
        self.pod_id = pod_id
        self._box = home_box(team)
        self._armed_at: float | None = None

    @property
    def active(self) -> bool:
        return self._armed_at is not None

    def arm(self, now: float) -> None:
        """A spawn or respawn: the grace period starts (again)."""
        self._armed_at = now

    def observe(self, pos: WirePos, now: float) -> None:
        """The pod reported a position: grace ends if it is strictly outside the home box."""
        if self._armed_at is None:
            return
        x_low, z_low, x_high, z_high = self._box
        x, z = pos.world_x, pos.world_z
        if x < x_low or x > x_high or z < z_low or z > z_high:
            self._end(now, "left the home box")
        else:
            self.tick(now)

    def tick(self, now: float) -> None:
        """Grace ends after ``GRACE_TIMEOUT`` seconds whatever the pod does."""
        if self._armed_at is not None and now - self._armed_at >= policy.GRACE_TIMEOUT:
            self._end(now, "timed out")

    def _end(self, now: float, why: str) -> None:
        assert self._armed_at is not None
        log.info(
            "pod %d: spawn grace ended after %.1f s (%s)", self.pod_id, now - self._armed_at, why
        )
        self._armed_at = None
