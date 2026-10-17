"""Logistics: orders flown in by the team's guppy (I §9.4, I §7.3, I §9.4.3, II §14).

This module decides and the city sends: every method returns *actions* (a guppy to create, a
destination to send, a job delivered) and the city turns them into events for the clients that
should hear them. It holds the team guppies and their job queues; what each *client* has been told
(its leg count) lives in its ``ClientView`` (DESIGN §3.5).

One guppy per team (id ``GUPPY_BASE_ID + team``), waiting at the center of its corner tile, one job
at a time. A priority job goes ahead of every waiting non-priority job and behind earlier priority
ones, and never displaces the job in flight. Jobs are a type (``Job``) so M8 adds module deliveries
and Air Support beside the tower.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto

from opencs import policy
from opencs.messages import ServerMessage, WirePos, WireXZ, server
from opencs.wire.codec import CELL_SIZE

#: A tile of the 3x3 around an enemy corner is within this many tiles of it. I §9.4.
NO_DROP_RADIUS_TILES = 1


def tile_of(world_x: int, world_z: int) -> tuple[int, int]:
    """The 600 ft tile holding a point; negative and 8 or more are off the map (I §6.1)."""
    return world_x // CELL_SIZE, world_z // CELL_SIZE


def in_enemy_no_drop_zone(team: int, pos: WirePos) -> bool:
    """Whether ``pos`` is in the 3x3 tiles around any *other* team's home corner, occupied or not
    (I §9.4.3: the manual's "enemy home-corner map tile" with no word on empty teams;
    every other team's corner counts (ours); see I §9.4.3). The off-map tiles beside a corner are
    part of its 3x3."""
    tx, tz = tile_of(pos.world_x, pos.world_z)
    return any(
        abs(tx - cx) <= NO_DROP_RADIUS_TILES and abs(tz - cz) <= NO_DROP_RADIUS_TILES
        for other, (cx, cz) in enumerate(policy.TEAM_CORNER_CELLS)
        if other != team
    )


def home_point(team: int) -> WireXZ:
    """The center of the team's corner tile: where its guppy is created and waits (I §9.4.3)."""
    cx, cz = policy.TEAM_CORNER_CELLS[team]
    return WireXZ.from_world(cx * CELL_SIZE + CELL_SIZE // 2, cz * CELL_SIZE + CELL_SIZE // 2)


@dataclass(frozen=True, slots=True)
class TowerJob:
    """Deliver a tower to the point the orderer stood at (I §9.4)."""

    team: int
    pos: WirePos
    mega: bool
    priority: bool
    orderer: int

    @property
    def destination(self) -> WireXZ:
        """Where the guppy flies: the ordered point, without its altitude."""
        return WireXZ(self.pos.cell_x, self.pos.cell_z, self.pos.x_off, self.pos.z_off)


#: What a guppy can be asked to carry. M8 adds module deliveries and Air Support.
type Job = TowerJob


class Phase(Enum):
    """Where a guppy is in its work."""

    ABSENT = auto()  # never created
    CREATING = auto()  # created, its create leg not yet reported
    HOME = auto()  # idle at home (the queue is empty)
    OUTBOUND = auto()  # flying a job to its point
    RETURNING = auto()  # flying home


@dataclass(frozen=True, slots=True)
class Create:
    """The guppy is needed for the first time: every playing client is introduced to it."""

    guppy: Guppy


@dataclass(frozen=True, slots=True)
class Dispatch:
    """Send the guppy to ``dest``: a ``'K'`` to every client that counts legs for it."""

    guppy: Guppy
    dest: WireXZ


type Action = Create | Dispatch


class Guppy:
    """One team's delivery vehicle, as the server tracks it: persistent, redispatched, never
    recreated (I §9.4)."""

    def __init__(self, team: int) -> None:
        self.team = team
        self.phase = Phase.ABSENT
        self.job: Job | None = None
        self.queue: list[Job] = []
        #: Legs sent so far; the create is leg 0. A client's n-th report is about leg n, so the
        #: leg in force is ``legs - 1`` (I §9.4.1, I §7.3).
        self.legs = 0
        self.dest = home_point(team)

    @property
    def entity_id(self) -> int:
        return policy.GUPPY_BASE_ID + self.team

    def creating_messages(self, now: float) -> Sequence[ServerMessage]:
        """``'K'`` with a new id creates the guppy at its home corner (I §9.4.3)."""
        return (server.MobileCreate(self.entity_id, home_point(self.team)),)

    def enqueue(self, job: Job) -> None:
        """Queue behind the waiting jobs, or, for a priority job, behind the earlier priority ones
        but ahead of every non-priority one (I §9.4.2, II §14)."""
        at = len(self.queue)
        if job.priority:
            at = next((i for i, queued in enumerate(self.queue) if not queued.priority), at)
        self.queue.insert(at, job)


class Logistics:
    """Every team's guppy and job queue."""

    def __init__(self) -> None:
        self._guppies: dict[int, Guppy] = {}

    def guppy(self, team: int) -> Guppy | None:
        return self._guppies.get(team)

    def queue_full(self, team: int) -> bool:
        """Whether a tower order from ``team`` meets ``MAX_WAITING_TOWER_ORDERS`` waiting jobs
        (II §14)."""
        guppy = self._guppies.get(team)
        return guppy is not None and len(guppy.queue) >= policy.MAX_WAITING_TOWER_ORDERS

    def order(self, job: Job) -> list[Action]:
        """Accept a job. The guppy is created the first time it is needed; a job is started
        only when the guppy is idle at home. Otherwise it waits: an order that arrives while the
        guppy flies home waits for the home report, because a destination replaced before it is
        reached is never reported (I §9.4.1, I §9.4.3)."""
        guppy = self._guppies.setdefault(job.team, Guppy(job.team))
        actions: list[Action] = []
        if guppy.phase is Phase.ABSENT:
            guppy.phase = Phase.CREATING
            guppy.legs = 1
            actions.append(Create(guppy))
        if guppy.phase is Phase.HOME:
            actions.append(self._start(guppy, job))
        else:
            guppy.enqueue(job)
        return actions

    def _start(self, guppy: Guppy, job: Job) -> Dispatch:
        guppy.job = job
        guppy.phase = Phase.OUTBOUND
        guppy.legs += 1
        guppy.dest = job.destination
        return Dispatch(guppy, guppy.dest)
