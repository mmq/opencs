"""Modules in the world: what may be ordered, and the drops lying about (I §9.4.2, I §9.3.1, I
§9.3.3).

A module is ordered with ``'E'``-``'P'`` and delivered as a dropped module (``'P'``) that any client
may pick up. This module holds the order rules (what a city stocks), the drop point and the registry
of drops; ``orders`` queues the guppy delivery.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass

from opencs import policy
from opencs.messages import ServerMessage, WirePos, server
from opencs.messages.catalog import CATALOG, is_module_index
from opencs.world.events import Event, ToPlayer
from opencs.world.host import WorldHost

log = logging.getLogger("opencs.world")

#: Orientation is in tenths of a degree (I §6.1).
_TENTHS_PER_RADIAN = 1800 / math.pi


@dataclass(frozen=True, slots=True)
class ModuleRules:
    """What one city stocks: every module up to ``max_level`` except the banned ones (II §15, II
    §5). Stock itself is unlimited."""

    max_level: int
    banned: frozenset[int]

    def stocks(self, module: int) -> bool:
        """Whether an order for ``module`` is accepted: a catalog index, within the city's level,
        not banned. The level is the catalog's, not the one the client sent."""
        row = CATALOG.get(module)
        return row is not None and row.level <= self.max_level and module not in self.banned


def drop_point(pos: WirePos, orient: int) -> WirePos:
    """Where a module ordered at ``pos`` facing ``orient`` appears: ``MODULE_DROP_AHEAD_FEET`` ahead
    along (sin, cos) in (x, z) and ``MODULE_DROP_HEIGHT`` above (I §6.1, I §9.4)."""
    theta = orient / _TENTHS_PER_RADIAN
    x = pos.world_x + round(policy.MODULE_DROP_AHEAD_FEET * math.sin(theta))
    z = pos.world_z + round(policy.MODULE_DROP_AHEAD_FEET * math.cos(theta))
    return WirePos.from_world(x, pos.y + policy.MODULE_DROP_HEIGHT, z)


@dataclass(slots=True)
class Drop:
    """A module lying in the world: an id, which module, where it is and how it was launched."""

    entity_id: int
    module: int
    pos: WirePos
    vx: int = 0
    vy: int = 0
    vz: int = 0

    def creating_messages(self, now: float) -> Sequence[ServerMessage]:
        """``'P'``: a late joiner gets the drop as it now lies (I §7.3)."""
        return (
            server.SpawnModule(self.entity_id, self.module, self.pos, self.vx, self.vy, self.vz),
        )


class Drops:
    """One city's dropped modules."""

    def __init__(self, host: WorldHost) -> None:
        self._host = host
        #: Every drop, by id, oldest first.
        self.drops: dict[int, Drop] = {}

    def reset(self) -> None:
        self.drops.clear()

    def create(
        self, module: int, pos: WirePos, now: float, velocity: tuple[int, int, int] = (0, 0, 0)
    ) -> list[Event]:
        """A module appears: every client that has reported is sent its ``'P'`` and a later joiner
        is given it by the sync (I §7.3, I §9.3.3). The oldest drop goes when the city holds
        ``MAX_DROPS``; no id left means nothing appears."""
        assert is_module_index(module)
        events: list[Event] = []
        if len(self.drops) >= policy.MAX_DROPS:
            events += self._remove(next(iter(self.drops.values())), now)
        drop_id = self._host.allocate_structure_id()
        if drop_id is None:
            log.warning("no free structure id; a dropped module %d was not placed", module)
            return events
        drop = Drop(drop_id, module, pos, *velocity)
        self.drops[drop_id] = drop
        log.info("module %d (%s) dropped as %d", module, CATALOG[module].name, drop_id)
        return events + self._host.spawn(drop, now)

    def _remove(self, drop: Drop, now: float, *, except_player: int | None = None) -> list[Event]:
        """``'Q'`` destroy to each client that holds the drop (but ``except_player``), then forget
        it and release its id. The holders are named now: forgetting the drop leaves
        ``ToHolders`` nobody to reach."""
        events = [
            Event(server.DestroyObject(drop.entity_id), ToPlayer(p.id))
            for p in self._host.players()
            if p.id != except_player and p.view.holds(drop.entity_id)
        ]
        self._host.despawn(drop.entity_id)
        del self.drops[drop.entity_id]
        self._host.release_structure_id(drop.entity_id, now)
        return events
