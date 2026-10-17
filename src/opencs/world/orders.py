"""Orders and what they deliver: towers and modules (I §9.4, II §10, II §15, II §14; DESIGN §3.1).

The glue between a client's orders, the team guppies (``logistics``) and what lands: answering an
order, turning the guppy's decisions into events for the clients that should hear them, placing a
tower, dropping a module, reducing a tower by hit reports and destroying it. ``City`` owns one
``Orders`` and delegates to it; it asks the city, through ``WorldHost``, for the things only the
city has (the roster, spawning, the structure ids).
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from opencs import policy
from opencs.messages import ClientMessage, WirePos, client, server
from opencs.messages.catalog import EFFECT_MODULES, is_module_index
from opencs.messages.fields import GRENADE_KIND
from opencs.world.events import Event, ToPlayer, ToPlayers
from opencs.world.host import WorldHost
from opencs.world.logistics import (
    Action,
    AirSupportJob,
    CatchUp,
    Create,
    Deliver,
    Dispatch,
    Logistics,
    ModuleJob,
    TowerJob,
    in_enemy_no_drop_zone,
    in_home_zone,
)
from opencs.world.modules import Drops, ModuleRules, drop_point
from opencs.world.player import Player
from opencs.world.structures import Tower, TowerHit
from opencs.world.territory import Territory

log = logging.getLogger("opencs.world")


@dataclass(frozen=True, slots=True)
class Strike:
    """An Air Support grenade dropped: when, by whose team's guppy, where (I §9.3.6). M10's resume
    reads these as enemy strikes near a dropped pod (II §13 item 3)."""

    at: float
    team: int
    pos: WirePos


class Orders:
    """One city's guppies, job queues and standing towers."""

    def __init__(
        self, host: WorldHost, territory: Territory, rules: ModuleRules, drops: Drops
    ) -> None:
        self._host = host
        self._territory = territory
        self._rules = rules
        self._drops = drops
        #: The team guppies and their job queues (II §14).
        self.logistics = Logistics()
        #: Every tower standing, by id (a subset of the city's live entities): what hit reports
        #: reduce (II §10).
        self.towers: dict[int, Tower] = {}
        #: Hit reports on a tower with a damage byte past the client's table (I §8.11): ignored
        #: and counted, apart from the pod-damage relay's own counter.
        self.ignored_tower_damage = 0
        #: The last Air Support strikes, newest last (II §13 item 3).
        self.strikes: deque[Strike] = deque(maxlen=policy.STRIKE_LOG_MAX)

    def reset(self) -> None:
        """The city emptied: its towers, guppies and queues go with it (II §17)."""
        self.towers.clear()
        self.logistics = Logistics()
        self.strikes.clear()

    # --- orders ------------------------------------------------------------------------------

    def tower_order(self, player: Player, order: client.TowerOrder, now: float) -> list[Event]:
        """A tower order (I §9.4.3, I §9.4.3): refused with ``'O'`` 1 inside the 3x3 tiles around an
        enemy home corner and 2 when the team's queue is full (the client refunds its point on
        either); otherwise ``'O'`` 0 to the orderer alone (I App. B) and a job for the team's
        guppy."""
        if not player.pod.has_reported:
            return []
        if in_enemy_no_drop_zone(player.team, order.pos):
            return [_reply(player, policy.ORDER_BAD_DROP_POINT)]
        if self.logistics.queue_full(player.team):
            return [_reply(player, policy.ORDER_QUEUE_FULL)]
        job = TowerJob(player.team, order.pos, order.mega == 1, order.priority != 0, player.id)
        reply = _reply(player, policy.ORDER_ACCEPTED)
        return [reply, *self._perform(self.logistics.order(job), now)]

    def refuse_order(self, player: Player, message: ClientMessage) -> list[Event]:
        """An order the session would not pass on (over the order budget): a tower order is
        answered ``'O'`` 2 so the client refunds the point it spent on sending it; a module order
        ``'E'``-``'P'`` ok = 0, which refunds nothing because it cost nothing (I §9.4.2). Nothing is
        queued."""
        if not player.pod.has_reported:
            return []
        if isinstance(message, client.ModuleOrder):
            if not is_module_index(message.module):
                return []  # not a catalog index: a modified client, nothing to answer
            return _module_reply(player, message.module, policy.MODULE_ORDER_REFUSED)
        return [_reply(player, policy.ORDER_QUEUE_FULL)]

    def module_order(self, player: Player, order: client.ModuleOrder, now: float) -> list[Event]:
        """A module purchase (I §9.4.2, II §15, I §9.4.3). The reply goes to the orderer alone:
        ok = 0 and nothing delivered when the city does not stock it (not a catalog index, above
        the city's level, banned) or the orderer stands in the 3x3 tiles around an enemy home
        corner (never ``'O'``: its refusals refund a point a module never cost). Otherwise ok = 1,
        and the module drops at once if the orderer is in its team's home zone, else the team's
        guppy flies it in. The point is fixed now, from the orderer's last reported place."""
        if not player.pod.has_reported or not is_module_index(order.module):
            return []  # not a catalog index: a modified client
        placed = player.pod.placed
        if placed is None:
            return []  # a 'J' before the first 'b' leaves a pod that has reported but is unplaced
        no_stock = not self._rules.stocks(order.module)
        no_drop = in_enemy_no_drop_zone(player.team, placed.pos)
        home = in_home_zone(player.team, placed.pos)
        if no_stock or no_drop or (not home and self.logistics.queue_saturated(player.team)):
            return _module_reply(player, order.module, policy.MODULE_ORDER_REFUSED)
        reply = _module_reply(player, order.module, policy.MODULE_ORDER_ACCEPTED)
        point = drop_point(placed.pos, placed.orient)
        if home:
            return reply + self._drops.create(order.module, point, now)
        job = ModuleJob(player.team, order.module, point, order.priority != 0, player.id)
        return reply + self._perform(self.logistics.order(job), now)

    def air_support(self, player: Player, request: client.AirSupport, now: float) -> list[Event]:
        """``'E'``-``'A'`` (I §9.3.5): acknowledged at once with ``'E'``-``'A'`` 0 ("Rgr, Air
        Support. CLEAR AREA!"), and a job queued for the team's guppy (priority honored, not
        refused in a no-drop zone: the rule names tower drops). On arrival it drops a grenade at the
        point, at the requester's last reported altitude. A team queue at the safety bound
        acknowledges but queues nothing."""
        placed = player.pod.placed
        if placed is None:
            return []
        if not self._rules.stocks(EFFECT_MODULES["A"]):
            log.info("Air Support from player %d: this city does not stock it", player.id)
            return []
        reply = Event(server.AirSupportReply(policy.AIR_SUPPORT_ACKNOWLEDGED), ToPlayer(player.id))
        if self.logistics.queue_saturated(player.team):
            log.warning("team %d guppy queue full; an Air Support request was dropped", player.team)
            return [reply]
        xz = request.pos
        point = WirePos(xz.cell_x, xz.cell_z, xz.x_off, placed.pos.y, xz.z_off)
        job = AirSupportJob(player.team, point, request.priority != 0, player.id)
        return [reply, *self._perform(self.logistics.order(job), now)]

    def leg_arrived(self, player: Player, arrival: client.LegArrived, now: float) -> list[Event]:
        """A client's copy of a guppy completed a leg (I §9.4.1). Only guppies this client holds
        are counted: a Muncher's report (M9) or a made-up id is ignored."""
        count = player.view.legs.get(arrival.id)
        if count is None:
            return []
        return self._perform(self.logistics.report(arrival.id, player.id, count), now)

    def _perform(self, actions: Sequence[Action], now: float) -> list[Event]:
        """Turn the guppy's decisions into events for the clients that should hear them."""
        events: list[Event] = []
        for action in actions:
            match action:
                case Create(guppy):
                    events += self._host.spawn(guppy, now)
                case Deliver(TowerJob() as job):
                    events += self.place_tower(job.team, job.pos, job.mega, now)
                case Deliver(ModuleJob() as job):
                    events += self._drops.create(job.module, job.pos, now)
                case Deliver(AirSupportJob() as job):
                    events += self._strike(job, now)
                case Dispatch(guppy, dest):
                    # Only clients that count legs for it; one still joining is sent no 'K' for
                    # this guppy until its copy has reported (I §7.3 step 2).
                    for p in self._host.players():
                        count = p.view.legs.get(guppy.entity_id)
                        if count is not None and not count.joining:
                            count.owed += 1  # it will report this leg (I §9.4.1)
                            events.append(
                                Event(server.MobileCreate(guppy.entity_id, dest), ToPlayer(p.id))
                            )
                case CatchUp(guppy, player_id, dest):
                    for p in self._host.players():
                        if p.id == player_id:
                            p.view.legs[guppy.entity_id].owed += 1
                    events.append(
                        Event(server.MobileCreate(guppy.entity_id, dest), ToPlayer(player_id))
                    )
        return events

    def _strike(self, job: AirSupportJob, now: float) -> list[Event]:
        """The guppy arrived: a grenade (``'c'`` KIND 9, shooter = the guppy) at the point, spawned
        level so it detonates at once, to every client past its load (I §9.3.6, I App. B). Damage is
        the clients' own. Logged as a shot fired."""
        guppy_id = policy.GUPPY_BASE_ID + job.team
        self.strikes.append(Strike(now, job.team, job.pos))
        log.info("air strike by guppy %d at %s", guppy_id, job.pos)
        playing = frozenset(p.id for p in self._host.players() if p.pod.has_reported)
        shot = server.Shot(guppy_id, job.pos, 0, 0, GRENADE_KIND)
        return [Event(shot, ToPlayers(playing))]

    # --- towers ------------------------------------------------------------------------------

    def place_tower(self, team: int, pos: WirePos, mega: bool, now: float) -> list[Event]:
        """A tower lands for ``team`` at its ordered point: every playing client is sent its ``'M'``
        and a later joiner is given it by the sync (I §7.3). No id left: nothing is placed."""
        tower_id = self._host.allocate_structure_id()
        if tower_id is None:
            log.warning("no free structure id; a tower for team %d was not placed", team)
            return []
        tower = Tower.new(tower_id, team, pos, mega)
        self.towers[tower_id] = tower
        log.info("tower %d placed for team %d in block %s", tower_id, team, tower.block)
        events = self._host.spawn(tower, now)
        self.log_territory()
        return events

    def hit_tower(
        self, reporter: Player, tower: Tower, hit: client.HitReport, now: float
    ) -> list[Event]:
        """Subtract the damage byte's table entry, whoever fired and whatever their team (II §9.1).
        A weapon class past the client's table is ignored."""
        match tower.take_damage(hit.damage):
            case TowerHit.IGNORED:
                self.ignored_tower_damage += 1
                level = logging.WARNING if self.ignored_tower_damage == 1 else logging.DEBUG
                log.log(
                    level, "tower hit with damage byte %d by %d ignored", hit.damage, reporter.id
                )
                return []
            case TowerHit.DESTROYED:
                return self.destroy_tower(tower, now)
            case TowerHit.STANDS:
                return []

    def destroy_tower(self, tower: Tower, now: float) -> list[Event]:
        """``'J'`` to every client that holds it, then forget it: a later joiner is not given its
        create, and its id is released for reuse (I §6.2, II §8, II §10). The holders are named now:
        forgetting it leaves ``ToHolders`` nobody to reach."""
        events = [
            Event(server.Destroy(tower.entity_id), ToPlayer(p.id))
            for p in self._host.players()
            if p.view.holds(tower.entity_id)
        ]
        self._host.despawn(tower.entity_id)
        del self.towers[tower.entity_id]
        self._host.release_structure_id(tower.entity_id, now)
        log.info("tower %d of team %d destroyed", tower.entity_id, tower.team)
        self.log_territory()
        return events

    def territory_income(self) -> dict[int, int]:
        """Team -> the points per second (before multipliers) its controlled blocks are worth, from
        the towers the server knows. The clients compute their own income; this is for the
        server's log and, in M9, for the free Muncher's target (I §9.2.6)."""
        territory = self._territory
        controlled = territory.controllers((t.block, t.team) for t in self.towers.values())
        return territory.income(controlled)

    def log_territory(self) -> None:
        log.info("territory income per second by team: %s", self.territory_income())


def _reply(player: Player, result: int) -> Event:
    """An ``'O'`` to the orderer alone (I App. B)."""
    return Event(server.OrderReply(result), ToPlayer(player.id))


def _module_reply(player: Player, module: int, ok: int) -> list[Event]:
    """An ``'E'``-``'P'`` to the orderer alone (I App. B)."""
    return [Event(server.ModuleReply(module, ok), ToPlayer(player.id))]
