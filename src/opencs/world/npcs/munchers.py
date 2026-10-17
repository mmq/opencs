"""Tower Munchers: a neutral tower eater (I §9.5.2, II §16.2, I §7.3, II §16.2).

What the client does. ``'K'`` with a new id >= 260 creates a Muncher at the point, 325 ft up, with
its destination zeroed; ``'K'`` with a known id sends it there, abandoning the old leg unreported;
every client reports each arrival with ``'K'``, once per leg *dispatched* (the create is never
reported); ``'E'``-``'C'`` ``muncher target`` brings it down onto the tower and latches it, with no
report. The client never damages anything. Everything else is ours.

What the server does. A Muncher flies to the nearest tower that is not Immunized within 600 ft of
itself (any team's), is latched on it for ``MUNCHER_EAT_SECONDS``, then the tower is destroyed and
it goes on from there; with nothing left it flies to the nearest city edge and is removed with
``'J'`` (``'H'`` cannot carry its id). Call Muncher makes one at the caller.

Per client. A Muncher's ``'K'`` and ``'E'``-``'C'`` go only to the clients whose copy is in step
with it: to any other client a ``'K'`` is a new id and makes a stray Muncher (I App. B). A client
introduced later is given its own create and an immediate dispatch, created where the current leg
began (on the tower itself if the Muncher is eating); until that copy's first report it is
*joining* and its reports drive nothing. That first report is answered with the next leg or, if the
Muncher is eating, the latch. The create and the first dispatch are one ``creating_messages`` so
they travel the client's queue together and in order (a dispatch that overtook the create would
create the Muncher at the wrong place).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto

from opencs import policy
from opencs.messages import ServerMessage, WirePos, WireXZ, server
from opencs.wire.codec import CITY_SIZE
from opencs.world.events import Event, ToPlayer, ToPlayers
from opencs.world.host import WorldHost
from opencs.world.ids import IdRange
from opencs.world.orders import Orders
from opencs.world.player import Player
from opencs.world.structures import Tower, damage_of
from opencs.world.view import ClientView, MuncherSync

log = logging.getLogger("opencs.world")


class Phase(Enum):
    """What a Muncher is doing."""

    TRAVEL = auto()  # flying to its target tower
    EATING = auto()  # latched on it
    LEAVING = auto()  # flying to the city edge


def nearest_edge(x: int, z: int) -> tuple[int, int]:
    """The point just inside the city edge nearest ``(x, z)`` (II §16.2)."""
    x = min(max(x, 0), CITY_SIZE)
    z = min(max(z, 0), CITY_SIZE)
    inset = policy.MUNCHER_EDGE_INSET_FEET
    gaps = {"w": x, "e": CITY_SIZE - x, "n": z, "s": CITY_SIZE - z}
    side = min(gaps, key=lambda k: gaps[k])
    return {
        "w": (inset, z),
        "e": (CITY_SIZE - inset, z),
        "n": (x, inset),
        "s": (x, CITY_SIZE - inset),
    }[side]


@dataclass(slots=True)
class Muncher:
    """One Muncher as the server tracks it. ``(x, z)`` is where its current leg ends and
    ``(from_x, from_z)`` where it began, in world feet. ``legs`` counts dispatches including the
    first; ``acted`` is the highest leg whose arrival has been acted on."""

    entity_id: int
    x: int
    z: int
    from_x: int
    from_z: int
    free: bool = False
    phase: Phase = Phase.TRAVEL
    target: int | None = None
    eat_until: float = 0.0
    legs: int = 0
    acted: int = 0
    hp: int = policy.MUNCHER_HP

    @property
    def destination(self) -> WireXZ:
        return WireXZ.from_world(self.x, self.z)

    def creating_messages(self, now: float) -> Sequence[ServerMessage]:
        """The create where the current leg began (on the tower, if eating) and, at once, a
        dispatch to where it is going (I §9.5.2, II §16.2, I §7.3)."""
        fx, fz = (self.x, self.z) if self.phase is Phase.EATING else (self.from_x, self.from_z)
        return (
            server.MobileCreate(self.entity_id, WireXZ.from_world(fx, fz)),
            server.MobileCreate(self.entity_id, self.destination),
        )

    def client_introduced(self, view: ClientView) -> None:
        """A client was sent the create and the dispatch. While the first leg is still the one in
        force it counts like everyone; otherwise it is *joining* (I §7.3)."""
        view.munchers[self.entity_id] = MuncherSync(
            joining=not (self.legs == 1 and self.acted == 0), sent_leg=self.legs, owed=1
        )


class Munchers:
    """One city's Munchers."""

    def __init__(
        self,
        host: WorldHost,
        orders: Orders,
    ) -> None:
        self._host = host
        self._orders = orders
        #: Every Muncher flying, by id.
        self.alive: dict[int, Muncher] = {}
        #: Leg reports from a client with no leg owed (I §9.4.1): out of protocol, counted.
        self.ignored_reports = 0

    def reset(self) -> None:
        """The city emptied: nothing carries over (II §17)."""
        self.alive.clear()

    # --- making them -------------------------------------------------------------------------

    def call(self, here: WirePos, now: float) -> list[Event]:
        """``'E'``-``'C'`` from a client: a Muncher at the caller, going for the nearest tower in
        reach (II §16.2)."""
        if len(self.alive) >= policy.MAX_MUNCHERS:
            log.warning("%d Munchers already; a Call Muncher was ignored", len(self.alive))
            return []
        return self._create(here.world_x, here.world_z, free=False, target=None, now=now)

    def _create(
        self, x: int, z: int, *, free: bool, target: Tower | None, now: float
    ) -> list[Event]:
        entity_id = self._host.allocate_id(IdRange.MUNCHER)
        if entity_id is None:
            log.warning("no free Muncher id; none created")
            return []
        m = Muncher(entity_id, x, z, x, z, free=free)
        if target is None:
            self._aim(m, now)
        else:
            self._aim_at(m, target)
        self.alive[entity_id] = m
        log.info(
            "%s Muncher %d created at (%d, %d), %s",
            "free" if free else "called",
            entity_id,
            x,
            z,
            "leaving" if m.target is None else f"for tower {m.target}",
        )
        return self._host.spawn(m, now)

    # --- choosing a leg ----------------------------------------------------------------------

    def _edible(self, m: Muncher, now: float) -> list[Tower]:
        """Towers within ``MUNCHER_HUNT_FEET`` of the Muncher that are not Immunized, nearest
        first (id order breaks a tie): any team's (II §16.2)."""
        reach = policy.MUNCHER_HUNT_FEET
        found = [
            (math.hypot(t.pos.world_x - m.x, t.pos.world_z - m.z), t.entity_id, t)
            for t in self._orders.towers.values()
            if not t.immune(now)
        ]
        return [t for d, _, t in sorted(found, key=lambda f: f[:2]) if d <= reach]

    def _aim(self, m: Muncher, now: float) -> None:
        """Choose the next leg: the nearest edible tower, else the nearest city edge."""
        food = self._edible(m, now)
        if food:
            self._aim_at(m, food[0])
            return
        m.from_x, m.from_z = m.x, m.z
        m.target, m.phase = None, Phase.LEAVING
        m.x, m.z = nearest_edge(m.x, m.z)
        m.legs += 1

    def _aim_at(self, m: Muncher, tower: Tower) -> None:
        m.from_x, m.from_z = m.x, m.z
        m.target, m.phase = tower.entity_id, Phase.TRAVEL
        m.x, m.z = tower.pos.world_x, tower.pos.world_z
        m.legs += 1

    # --- audiences ---------------------------------------------------------------------------

    def _synced(self, m: Muncher) -> list[Player]:
        """The players whose copy of ``m`` is in step: live traffic goes to them only. Named
        explicitly rather than ``ToHolders``: a joining client holds the Muncher too (I App. B)."""
        return [
            p
            for p in self._host.players()
            if (sync := p.view.munchers.get(m.entity_id)) is not None and not sync.joining
        ]

    def _advance(self, m: Muncher, now: float) -> list[Event]:
        """Choose the next leg and send the dispatch to the clients in step."""
        self._aim(m, now)
        events: list[Event] = []
        for p in self._synced(m):
            p.view.munchers[m.entity_id].owed += 1  # it will report this leg
            events.append(Event(server.MobileCreate(m.entity_id, m.destination), ToPlayer(p.id)))
        return events

    # --- the client's reports ----------------------------------------------------------------

    def owns(self, entity_id: int) -> bool:
        return entity_id in self.alive

    def arrived(self, player: Player, muncher_id: int, now: float) -> list[Event]:
        """A client reported that its copy of a Muncher completed a leg (I §9.5.2). The first
        report of each leg acts; the other clients' are duplicates. A client that holds no copy,
        or has no leg owed, drives nothing."""
        m = self.alive.get(muncher_id)
        sync = player.view.munchers.get(muncher_id)
        if m is None or sync is None:
            return []
        if sync.owed <= 0:
            self.ignored_reports += 1
            return []
        sync.owed -= 1
        if sync.joining:
            return self._joining_report(m, player, sync, now)
        return self._counted_report(m, sync, now)

    def _counted_report(self, m: Muncher, sync: MuncherSync, now: float) -> list[Event]:
        sync.reports += 1
        leg = sync.reports
        if leg != m.legs or m.acted >= leg:
            return []  # an old leg, or one another client reported first
        m.acted = leg
        if m.phase is Phase.LEAVING:
            log.info("Muncher %d left the city", m.entity_id)
            return self._remove(m, now)
        tower = self._orders.towers.get(m.target) if m.target is not None else None
        if tower is None or tower.immune(now):
            return self._advance(m, now)  # eaten by someone else, or Immunized meanwhile
        m.phase, m.eat_until = Phase.EATING, now + policy.MUNCHER_EAT_SECONDS
        return [
            Event(server.SendMuncher(m.entity_id, tower.entity_id), ToPlayer(p.id))
            for p in self._synced(m)
        ]

    def _joining_report(
        self, m: Muncher, player: Player, sync: MuncherSync, now: float
    ) -> list[Event]:
        """A joining client's copy reached the leg it was sent (I §7.3). If the Muncher has
        moved on, send it where it goes now and wait for the next report. Otherwise it is in
        step: while eating it is latched, else this report is the arrival of the current leg."""
        if sync.sent_leg != m.legs:
            sync.sent_leg = m.legs
            sync.owed += 1
            return [Event(server.MobileCreate(m.entity_id, m.destination), ToPlayer(player.id))]
        sync.joining = False
        if m.phase is Phase.EATING:
            assert m.target is not None
            sync.reports = m.legs
            return [Event(server.SendMuncher(m.entity_id, m.target), ToPlayer(player.id))]
        sync.reports = m.legs - 1  # this report is the arrival of the current leg
        return self._counted_report(m, sync, now)

    # --- time --------------------------------------------------------------------------------

    def tick(self, now: float) -> list[Event]:
        """Finish any meal that is due."""
        events: list[Event] = []
        for m in list(self.alive.values()):
            if m.phase is Phase.EATING and now >= m.eat_until:
                events += self._finish_meal(m, now)
        return events

    def _finish_meal(self, m: Muncher, now: float) -> list[Event]:
        """The tower is destroyed (unless it is gone or was Immunized meanwhile) and the Muncher
        goes on from where it stands."""
        events: list[Event] = []
        tower = self._orders.towers.get(m.target) if m.target is not None else None
        if tower is not None and not tower.immune(now):
            log.info("Muncher %d ate tower %d", m.entity_id, tower.entity_id)
            events += self._orders.destroy_tower(tower, now)
        return events + self._advance(m, now)

    # --- durability --------------------------------------------------------------------------

    def hit(self, target: int, damage_byte: int, now: float) -> list[Event] | None:
        """A hit report naming ``target``: None if it is not a Muncher. The damage byte's table
        entry comes off, whoever fired; at 0 the Muncher is removed with ``'J'`` (I §9.5.2)."""
        m = self.alive.get(target)
        if m is None:
            return None
        damage = damage_of(damage_byte)
        if damage is None:
            return []
        m.hp -= damage
        if m.hp > 0:
            return []
        log.info("Muncher %d destroyed", m.entity_id)
        return self._remove(m, now)

    def _remove(self, m: Muncher, now: float) -> list[Event]:
        """``'J'`` to every client that holds it (joining ones too), then forget it and free its
        id. The holders are named now: forgetting it leaves ``ToHolders`` nobody to reach."""
        holders = frozenset(p.id for p in self._host.players() if p.view.holds(m.entity_id))
        events = [Event(server.Destroy(m.entity_id), ToPlayers(holders))] if holders else []
        self._host.despawn(m.entity_id)
        del self.alive[m.entity_id]
        self._host.release_id(IdRange.MUNCHER, m.entity_id, now)
        return events
