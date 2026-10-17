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
from opencs.messages import WirePos, server
from opencs.messages.catalog import EFFECT_MODULES
from opencs.world.entities import inform
from opencs.world.events import Event, ToPlayer
from opencs.world.host import WorldHost
from opencs.world.modules import ModuleRules
from opencs.world.orders import Orders
from opencs.world.player import Player
from opencs.world.pods import PodPhase
from opencs.world.structures import Mine, Tower
from opencs.world.weather import Weather

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

    def __init__(
        self, host: WorldHost, orders: Orders, weather: Weather, rules: ModuleRules
    ) -> None:
        self._host = host
        self._rules = rules
        self._orders = orders
        self._weather = weather
        self._effects: dict[str, _Effect] = {
            "R": self._repair,
            "H": self._harden,
            "L": self._liberate,
            "I": self._immunize,
            "W": self._weather_change,
            "M": self._lay_mine,
            "D": self._sweep_mines,
        }
        #: Every mine laid, by id, oldest first. Hits on a mine are ignored: a mine has no
        #: durability (ours).
        self.mines: dict[int, Mine] = {}

    def reset(self) -> None:
        """The city emptied: nothing carries over (II §17)."""
        self.mines.clear()

    def request(self, player: Player, effect: str, now: float) -> list[Event]:
        """An ``'E'`` effect request from ``player`` (I §9.3.5, II §16.1). Ignored before the pod's
        first report and while it is dead, and when the city does not stock the module (an honest
        pod holds only modules this server delivered, so it is a forged request: logged, I §11);
        Call Muncher, Call Sentinel and anything not built are logged and ignored."""
        placed = player.pod.placed
        if placed is None or player.pod.phase is PodPhase.DEAD:
            return []
        if not self._rules.stocks(EFFECT_MODULES.get(effect, 0)):
            log.info("effect %r from player %d: this city does not stock it", effect, player.id)
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

    # --- the weather -------------------------------------------------------------------------

    def _weather_change(self, player: Player, here: WirePos, now: float) -> list[Event]:
        """Weather: the next condition, ``'R'`` sub 0 to every client past its load (the others
        are given the palette by their first-report sync, I §7.3). Ignored while a program's
        crossfade runs, so palette changes stay 50 s apart (I §8.20)."""
        if not self._weather.can_cycle(now):
            log.info("player %d used Weather during a crossfade; ignored", player.id)
            return []
        palette = self._weather.cycle(now)
        log.info("player %d changed the weather to %d", player.id, palette)
        events: list[Event] = []
        for p in self._host.players():
            if p.pod.has_reported:
                events += inform(self._weather, p.view, now)
        return events

    # --- mines -------------------------------------------------------------------------------

    def _lay_mine(self, player: Player, here: WirePos, now: float) -> list[Event]:
        """Mine: an IFF mine at the sender, ``'F'`` with its team as the model to every client,
        held for late joiners. The oldest goes when the city holds ``MAX_MINES``."""
        events: list[Event] = []
        if len(self.mines) >= policy.MAX_MINES:
            events += self._forget(next(iter(self.mines.values())), now)
        mine_id = self._host.allocate_structure_id()
        if mine_id is None:
            log.warning("no free structure id; a mine for team %d was not laid", player.team)
            return events
        mine = Mine(mine_id, player.team, here)
        self.mines[mine_id] = mine
        return events + self._host.spawn(mine, now)

    def _sweep_mines(self, player: Player, here: WirePos, now: float) -> list[Event]:
        """Mine Sweep: every mine of another team within one block destroyed, ``'J'`` to each
        holder, and forgotten."""
        events: list[Event] = []
        for mine in list(self.mines.values()):
            if mine.team != player.team and within_one_block(here, mine.pos):
                events += self._forget(mine, now)
        return events

    def _forget(self, mine: Mine, now: float) -> list[Event]:
        """``'J'`` to each client that holds the mine, then forget it and release its id. The
        holders are named now: forgetting the mine leaves ``ToHolders`` nobody to reach."""
        events = [
            Event(server.Destroy(mine.entity_id), ToPlayer(p.id))
            for p in self._host.players()
            if p.view.holds(mine.entity_id)
        ]
        self._host.despawn(mine.entity_id)
        del self.mines[mine.entity_id]
        self._host.release_structure_id(mine.entity_id, now)
        return events
