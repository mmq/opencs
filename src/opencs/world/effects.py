"""Effect-module requests the server carries out (I §9.3.5, II §16.1).

Each one-shot effect module sends one ``'E'`` sub and nothing else happens: the client changes
nothing locally. The effect applies at the sender's last reported position; "within one block" is
``ONE_BLOCK_FEET`` straight-line across the ground, measured to each tower's or mine's ordered point
(I §9.3.5, II §16.1). Call Muncher and Call Sentinel are M9's.
Air Support is a guppy job and lives with the other orders.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator

from opencs import policy
from opencs.messages import WirePos
from opencs.world.events import Event
from opencs.world.host import WorldHost
from opencs.world.orders import Orders
from opencs.world.player import Player
from opencs.world.pods import PodPhase
from opencs.world.structures import Tower

log = logging.getLogger("opencs.world")

type _Effect = Callable[[Player, WirePos, float], list[Event]]


def within_one_block(center: WirePos, other: WirePos) -> bool:
    """Whether ``other`` is within ``ONE_BLOCK_FEET`` of ``center``, in feet across the ground
    (I §9.3.5, II §16.1). Exactly 600 ft counts."""
    dx = center.world_x - other.world_x
    dz = center.world_z - other.world_z
    return dx * dx + dz * dz <= policy.ONE_BLOCK_FEET**2


class Effects:
    """One city's effect modules."""

    def __init__(self, host: WorldHost, orders: Orders) -> None:
        self._host = host
        self._orders = orders
        self._effects: dict[str, _Effect] = {
            "R": self._repair,
            "H": self._harden,
            "L": self._liberate,
            "I": self._immunize,
        }

    def request(self, player: Player, effect: str, now: float) -> list[Event]:
        """An ``'E'`` effect request from ``player`` (I §9.3.5, II §16.1). Ignored before the pod's
        first report and while it is dead; Call Muncher, Call Sentinel and anything not built are
        logged and ignored."""
        placed = player.pod.placed
        if placed is None or player.pod.phase is PodPhase.DEAD:
            return []
        handler = self._effects.get(effect)
        if handler is None:
            log.info("effect %r from player %d: not built yet", effect, player.id)
            return []
        return handler(player, placed.pos, now)

    # --- towers ------------------------------------------------------------------------------

    def _towers_near(
        self, here: WirePos, *, own_team: int | None, enemy_of: int | None = None
    ) -> Iterator[Tower]:
        """Towers within one block of ``here`` that belong to ``own_team``, or that do not belong
        to ``enemy_of``'s team, in id order."""
        for tower in sorted(self._orders.towers.values(), key=lambda t: t.entity_id):
            if own_team is not None and tower.team != own_team:
                continue
            if enemy_of is not None and tower.team == enemy_of:
                continue
            if within_one_block(here, tower.pos):
                yield tower

    def _repair(self, player: Player, here: WirePos, now: float) -> list[Event]:
        """Repair: the sender's team's towers within one block back to full."""
        for tower in self._towers_near(here, own_team=player.team):
            tower.repair()
        return []

    def _harden(self, player: Player, here: WirePos, now: float) -> list[Event]:
        """Harden: those towers' current and maximum hit points doubled, once per tower."""
        for tower in self._towers_near(here, own_team=player.team):
            tower.harden()
        return []

    def _immunize(self, player: Player, here: WirePos, now: float) -> list[Event]:
        """Immunize: those towers Muncher-immune for ``IMMUNIZE_SECONDS``."""
        for tower in self._towers_near(here, own_team=player.team):
            tower.immunize(now)
        return []

    def _liberate(self, player: Player, here: WirePos, now: float) -> list[Event]:
        """Liberator: every enemy tower within one block destroyed, ``'J'`` to each holder."""
        events: list[Event] = []
        for tower in list(self._towers_near(here, own_team=None, enemy_of=player.team)):
            events += self._orders.destroy_tower(tower, now)
        return events
