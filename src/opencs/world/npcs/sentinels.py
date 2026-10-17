"""Sentinels: the city's stationary guns (I §9.1.3, I §9.5.2, II §16.2, II §16.3).

A Sentinel is created by ``'N'`` and then fires on its own: the client simulates it, and its ``'c'``
arrive from whichever client's player is nearest (I §9.1.3), so the city only relays them. What is
ours is the population (II §16): how many are resident, where, when a destroyed one comes
back, and how much a Sentinel can take. Every one absorbs ``SENTINEL_HP`` on the client's per-shot
table and is destroyed with ``'J'`` at 0.

Resident Sentinels are placed when the city's game starts, one per block, outside the 3x3 tile areas
round the four home corners, 150-450 ft into the block, at random; a destroyed one is replaced
``sentinel_respawn`` seconds later at a fresh spot with a fresh id. A *called* Sentinel
(``'E'``-``'S'``) stands where the caller was and is not replaced.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Sequence
from dataclasses import dataclass

from opencs import policy
from opencs.messages import ServerMessage, WirePos, WireXZ, server
from opencs.wire.codec import CELL_SIZE, CITY_CELLS
from opencs.world.events import Event, ToPlayer
from opencs.world.host import WorldHost
from opencs.world.ids import IdRange
from opencs.world.structures import damage_of

log = logging.getLogger("opencs.world")


def eligible_blocks() -> list[tuple[int, int]]:
    """The blocks a resident Sentinel may stand in: every block outside the 3x3 tiles round any
    home corner, whether or not its team plays (II §16.3)."""
    return [
        (bx, bz)
        for bx in range(CITY_CELLS)
        for bz in range(CITY_CELLS)
        if all(max(abs(bx - cx), abs(bz - cz)) > 1 for cx, cz in policy.TEAM_CORNER_CELLS)
    ]


@dataclass(slots=True)
class Sentinel:
    """One Sentinel: where it stands and how much it can still take."""

    entity_id: int
    pos: WireXZ
    resident: bool
    hp: int = policy.SENTINEL_HP

    def creating_messages(self, now: float) -> Sequence[ServerMessage]:
        """``'N'``: a late joiner is sent the same message as first sent (I §7.3)."""
        return (server.SpawnSentinel(self.entity_id, self.pos),)


class Sentinels:
    """One city's Sentinels."""

    def __init__(self, host: WorldHost, count: int, respawn: float, rng: random.Random) -> None:
        self._host = host
        self._count = count
        self._respawn = respawn
        self._rng = rng
        #: Every Sentinel standing, by id.
        self.alive: dict[int, Sentinel] = {}
        #: When each missing resident Sentinel is due back.
        self._replacements: list[float] = []

    def reset(self) -> None:
        """The city emptied: nothing carries over (II §17)."""
        self.alive.clear()
        self._replacements.clear()

    # --- population --------------------------------------------------------------------------

    def start(self, now: float) -> list[Event]:
        """The city's game began: the resident Sentinels, in distinct blocks."""
        events: list[Event] = []
        for block in self._rng.sample(eligible_blocks(), self._count):
            events += self._place(block, now)
        return events

    def tick(self, now: float) -> list[Event]:
        """Replace each destroyed resident Sentinel whose time has come, at a fresh spot."""
        due = [t for t in self._replacements if t <= now]
        if not due:
            return []
        self._replacements = [t for t in self._replacements if t > now]
        events: list[Event] = []
        for _ in due:
            events += self._place(self._fresh_block(), now)
        return events

    def _fresh_block(self) -> tuple[int, int]:
        """A block no resident Sentinel stands in, or any eligible one if all are taken."""
        taken = {
            (s.pos.world_x // CELL_SIZE, s.pos.world_z // CELL_SIZE)
            for s in self.alive.values()
            if s.resident
        }
        free = [b for b in eligible_blocks() if b not in taken]
        return self._rng.choice(free or eligible_blocks())

    def _place(self, block: tuple[int, int], now: float) -> list[Event]:
        low, high = policy.SENTINEL_OFFSET_FEET
        x = block[0] * CELL_SIZE + self._rng.randint(low, high)
        z = block[1] * CELL_SIZE + self._rng.randint(low, high)
        return self._create(WireXZ.from_world(x, z), IdRange.SENTINEL, True, now)

    def call(self, here: WirePos, now: float) -> list[Event]:
        """``'E'``-``'S'``: a Sentinel at the caller's last position, id from the structure range,
        not replaced when destroyed. Past ``MAX_CALLED_SENTINELS`` the oldest goes."""
        events: list[Event] = []
        called = [s for s in self.alive.values() if not s.resident]
        if len(called) >= policy.MAX_CALLED_SENTINELS:
            events += self._destroy(called[0], now)
        pos = WireXZ.from_world(here.world_x, here.world_z)
        return events + self._create(pos, IdRange.STRUCTURE, False, now)

    def _create(self, pos: WireXZ, which: IdRange, resident: bool, now: float) -> list[Event]:
        entity_id = self._host.allocate_id(which)
        if entity_id is None:
            log.warning("no free id for a Sentinel in %s; none created", which.name)
            return []
        sentinel = Sentinel(entity_id, pos, resident)
        self.alive[entity_id] = sentinel
        log.info(
            "%s Sentinel %d stands at (%d, %d)",
            "resident" if resident else "called",
            entity_id,
            pos.world_x,
            pos.world_z,
        )
        return self._host.spawn(sentinel, now)

    # --- durability --------------------------------------------------------------------------

    def hit(self, target: int, damage_byte: int, now: float) -> list[Event] | None:
        """A hit report naming ``target``: None if it is not a Sentinel, else what the hit did.
        The damage byte's table entry comes off, whoever fired; at 0 the Sentinel is destroyed
        (II §16.3). A weapon class past the client's table is ignored (I §8.11)."""
        sentinel = self.alive.get(target)
        if sentinel is None:
            return None
        damage = damage_of(damage_byte)
        if damage is None:
            log.debug("Sentinel %d: damage byte %d ignored", target, damage_byte)
            return []
        sentinel.hp -= damage
        if sentinel.hp > 0:
            return []
        if sentinel.resident:
            self._replacements.append(now + self._respawn)
        return self._destroy(sentinel, now)

    def _destroy(self, sentinel: Sentinel, now: float) -> list[Event]:
        """``'J'`` to every client that holds it, then forget it and free its id. The holders are
        named now: forgetting it leaves ``ToHolders`` nobody to reach."""
        events = [
            Event(server.Destroy(sentinel.entity_id), ToPlayer(p.id))
            for p in self._host.players()
            if p.view.holds(sentinel.entity_id)
        ]
        self._host.despawn(sentinel.entity_id)
        del self.alive[sentinel.entity_id]
        which = IdRange.SENTINEL if sentinel.resident else IdRange.STRUCTURE
        self._host.release_id(which, sentinel.entity_id, now)
        log.info("Sentinel %d destroyed", sentinel.entity_id)
        return events
