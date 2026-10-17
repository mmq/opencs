"""Tower orders and the towers they deliver (I §9.4, II §10, II §14; DESIGN §3.1).

The glue between a client's orders, the team guppies (``logistics``) and the towers that land:
answering an order, turning the guppy's decisions into events for the clients that should hear
them, placing a tower, reducing it by hit reports and destroying it. ``City`` owns one ``Orders``
and delegates to it; it asks the city, through ``OrderHost``, for the things only the city has
(the roster, spawning, the structure ids). M8 adds module deliveries and Air Support here.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from opencs import policy
from opencs.messages import WirePos, client, server
from opencs.world.entities import Entity
from opencs.world.events import Event, ToPlayer
from opencs.world.logistics import (
    Action,
    CatchUp,
    Create,
    Deliver,
    Dispatch,
    Logistics,
    TowerJob,
    in_enemy_no_drop_zone,
)
from opencs.world.player import Player
from opencs.world.structures import Tower, TowerHit
from opencs.world.territory import Territory

log = logging.getLogger("opencs.world")


class OrderHost(Protocol):
    """What the orders need of the city that owns them."""

    def players(self) -> Sequence[Player]: ...

    def spawn(self, entity: Entity, now: float) -> list[Event]: ...

    def despawn(self, entity_id: int) -> None: ...

    def allocate_structure_id(self) -> int | None:
        """A free id in the structure range, or None if none is safely reusable (II §8)."""
        ...

    def release_structure_id(self, entity_id: int, now: float) -> None: ...


class Orders:
    """One city's guppies, job queues and standing towers."""

    def __init__(self, host: OrderHost, territory: Territory) -> None:
        self._host = host
        self._territory = territory
        #: The team guppies and their job queues (II §14).
        self.logistics = Logistics()
        #: Every tower standing, by id (a subset of the city's live entities): what hit reports
        #: reduce (II §10).
        self.towers: dict[int, Tower] = {}
        #: Hit reports on a tower with a damage byte past the client's table (I §8.11): ignored
        #: and counted, apart from the pod-damage relay's own counter.
        self.ignored_tower_damage = 0

    def reset(self) -> None:
        """The city emptied: its towers, guppies and queues go with it (II §17)."""
        self.towers.clear()
        self.logistics = Logistics()

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

    def refuse_order(self, player: Player) -> list[Event]:
        """A tower order the session would not pass on (over the order budget): answered ``'O'`` 2
        so the client refunds the point it spent on sending it (I §8.17, I §8.7); nothing is
        queued."""
        if not player.pod.has_reported:
            return []
        return [_reply(player, policy.ORDER_QUEUE_FULL)]

    def module_order(self, player: Player, order: client.ModuleOrder) -> list[Event]:
        """Module orders are M8's; until then the client is answered "no stock" so it is not left
        waiting (I §8.7)."""
        try:
            reply = server.ModuleReply(order.module, policy.MODULE_ORDER_UNAVAILABLE)
        except ValueError:  # not a catalog index: a modified client
            return []
        return [Event(reply, ToPlayer(player.id))]

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
                case Deliver(job):
                    events += self.place_tower(job.team, job.pos, job.mega, now)
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
                return self._destroy_tower(tower, now)
            case TowerHit.STANDS:
                return []

    def _destroy_tower(self, tower: Tower, now: float) -> list[Event]:
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
