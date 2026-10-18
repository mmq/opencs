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

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum, auto

from opencs import policy
from opencs.messages import ServerMessage, WirePos, WireXZ, server
from opencs.wire.codec import CELL_SIZE, CITY_CELLS
from opencs.world.view import ClientView, LegCount

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


def in_home_zone(team: int, pos: WirePos) -> bool:
    """Whether ``pos`` is in the team's own corner tile or the three off-map tiles beside it, where
    a module arrives at once with no guppy (I §9.4.3)."""
    cx, cz = policy.TEAM_CORNER_CELLS[team]
    tx, tz = tile_of(pos.world_x, pos.world_z)
    last = CITY_CELLS - 1
    off_x = -1 if cx == 0 else last + 1  # the off-map tile beside the corner, on each axis
    off_z = -1 if cz == 0 else last + 1
    return tx in (cx, off_x) and tz in (cz, off_z)


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


@dataclass(frozen=True, slots=True)
class ModuleJob:
    """Deliver ``module`` to ``pos``, the drop point fixed when it was ordered (I §9.4)."""

    team: int
    module: int
    pos: WirePos
    priority: bool
    orderer: int

    @property
    def destination(self) -> WireXZ:
        return WireXZ(self.pos.cell_x, self.pos.cell_z, self.pos.x_off, self.pos.z_off)


@dataclass(frozen=True, slots=True)
class AirSupportJob:
    """Fly to ``pos`` and drop a grenade there (I §9.3.6): the point is the request's, at the
    requester's last reported altitude."""

    team: int
    pos: WirePos
    priority: bool
    requester: int

    @property
    def destination(self) -> WireXZ:
        return WireXZ(self.pos.cell_x, self.pos.cell_z, self.pos.x_off, self.pos.z_off)


#: What a guppy can be asked to carry or do.
type Job = TowerJob | ModuleJob | AirSupportJob


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


@dataclass(frozen=True, slots=True)
class Deliver:
    """The guppy reached the job's point: hand over what it carries."""

    job: Job


@dataclass(frozen=True, slots=True)
class CatchUp:
    """A joining client's copy has reported and the guppy has moved on: send that one client the
    current destination (I §7.3 step 3)."""

    guppy: Guppy
    player_id: int
    dest: WireXZ


@dataclass(frozen=True, slots=True)
class WarpHome:
    """The guppy is teleported to its home corner (an operator's failsafe, M12): every client that
    holds it is sent ``'L'``, resets its leg count and will report its arrival at once."""

    guppy: Guppy


type Action = Create | Dispatch | Deliver | CatchUp | WarpHome


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
        #: The highest leg already acted on; -1 before the create leg is reported.
        self.acted = -1

    @property
    def entity_id(self) -> int:
        return policy.GUPPY_BASE_ID + self.team

    def creating_messages(self, now: float) -> Sequence[ServerMessage]:
        """``'K'`` with a new id creates the guppy at its home corner (I §9.4.3)."""
        return (server.MobileCreate(self.entity_id, home_point(self.team)),)

    def client_introduced(self, view: ClientView) -> None:
        """A client was sent the create (I §7.3 step 1). While the guppy is still on its create
        leg the client counts from leg 0 like everyone; otherwise it is *joining*."""
        view.legs[self.entity_id] = LegCount(joining=self.phase is not Phase.CREATING, owed=1)

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
        #: Leg reports that were for a leg the guppy is not on (I §9.4.1): a protocol violation
        #: or a lost leg, counted so the replay tests can assert none happens.
        self.ignored_reports = 0

    def guppy(self, team: int) -> Guppy | None:
        return self._guppies.get(team)

    def warp_home(self, team: int) -> list[Action] | None:
        """The failsafe for a guppy stranded by leg reports that fell out of step (II §14; our
        leg-report rules; see I §9.4.1): put it back at its corner as if it had just been created.

        The job it was flying stays first in its queue, so no order is lost. The state becomes
        that of a new guppy awaiting its create leg: leg 1 in force, nothing acted on. The
        caller resets every client's count to match (``LegCount`` reports 0, owed 1, in step),
        since each client reports the arrival of a warp at once (I §8.14), which is then
        that create leg. None if the team has no guppy yet."""
        guppy = self._guppies.get(team)
        if guppy is None or guppy.phase is Phase.ABSENT:
            return None
        if guppy.job is not None:
            guppy.queue.insert(0, guppy.job)
            guppy.job = None
        guppy.phase = Phase.CREATING
        guppy.legs = 1
        guppy.acted = -1
        guppy.dest = home_point(team)
        return [WarpHome(guppy)]

    def queue_full(self, team: int) -> bool:
        """Whether a tower order from ``team`` meets ``MAX_WAITING_TOWER_ORDERS`` waiting jobs
        (II §14)."""
        guppy = self._guppies.get(team)
        return guppy is not None and len(guppy.queue) >= policy.MAX_WAITING_TOWER_ORDERS

    def queue_saturated(self, team: int) -> bool:
        """Whether the team's queue is at the safety bound ``MAX_WAITING_JOBS`` (DESIGN §5)."""
        guppy = self._guppies.get(team)
        return guppy is not None and len(guppy.queue) >= policy.MAX_WAITING_JOBS

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

    def report(
        self,
        guppy_id: int,
        player_id: int,
        count: LegCount,
        others: Iterable[LegCount] = (),
    ) -> list[Action]:
        """A client reported that its copy of guppy ``guppy_id`` completed a leg (I §9.4.1).

        Matched to a leg by that client's own count, with no timing: its n-th report is leg n.
        The first report of a leg acts; the other clients' reports of it are duplicates. A report
        for a leg ahead of the one in force is out of step: counted in ``ignored_reports`` and
        dropped, which a client that follows the protocol never causes. So is a report beyond the
        legs this client has been sent (``LegCount.owed``): a client's count never runs ahead of
        what it was told, whatever it sends.

        ``others`` are the other clients' counts for this guppy. When a report acts, each of them
        that is in step but has not yet reported that leg is made *joining*: the next ``'K'`` would
        replace the leg its copy is still flying, which it would then never report (I §9.4.1), so
        it is sent nothing until its own report, which the joining path answers (I §7.3)."""
        guppy = next((g for g in self._guppies.values() if g.entity_id == guppy_id), None)
        if guppy is None or guppy.phase is Phase.ABSENT:
            return []
        if count.owed <= 0:
            self.ignored_reports += 1
            return []
        count.owed -= 1
        return self._counted_report(guppy, player_id, count, others)

    def _counted_report(
        self, guppy: Guppy, player_id: int, count: LegCount, others: Iterable[LegCount]
    ) -> list[Action]:
        """A report within the legs the client was sent, matched to a leg by its count."""
        if count.joining:
            # Step 4: while joining, a report drives nothing. The one that brings the client in
            # step is, when the guppy is in flight, the arrival of the current leg: it carries
            # on below as an ordinary report; at home it is just the client's create report.
            catch_up = self._join_step(guppy, player_id, count)
            if catch_up is not None:
                return [catch_up]
            if guppy.phase not in (Phase.OUTBOUND, Phase.RETURNING):
                return []
        leg = count.reports
        count.reports += 1
        if leg <= guppy.acted:
            return []  # someone else got there first
        if leg != guppy.legs - 1:
            self.ignored_reports += 1
            return []
        guppy.acted = leg
        for other in others:
            if other is not count and not other.joining and other.reports <= leg:
                other.joining, other.caught_up = True, None
        return self._leg_done(guppy)

    def _join_step(self, guppy: Guppy, player_id: int, count: LegCount) -> CatchUp | None:
        """A joining client's copy reported (I §7.3 step 3). If the guppy has moved on since the
        leg last given to this client (it is in flight, or a catch-up was sent and the guppy has
        changed leg since), return the catch-up that sends the current destination; the client's
        next report is about that leg. Otherwise the client is in step: count it in and return
        None. At home its copy is at home too, so its next report is the next leg; in flight,
        this report is the arrival of the current leg."""
        in_flight = guppy.phase in (Phase.OUTBOUND, Phase.RETURNING)
        current = guppy.legs - 1
        if count.caught_up != current and (in_flight or count.caught_up is not None):
            count.caught_up = current
            return CatchUp(guppy, player_id, guppy.dest)
        count.joining = False
        count.reports = current if in_flight else guppy.legs
        return None

    def _leg_done(self, guppy: Guppy) -> list[Action]:
        """The guppy's current leg is reported. A job's leg delivers it. Then the guppy takes the
        next waiting job, or, after a delivery with none waiting, flies home; reaching home (or
        finishing its create leg) with none waiting leaves it idle (I §9.4.1, I §9.4.3)."""
        actions: list[Action] = []
        delivered = guppy.phase is Phase.OUTBOUND
        if delivered:
            assert guppy.job is not None
            actions.append(Deliver(guppy.job))
            guppy.job = None
        if guppy.queue:
            actions.append(self._start(guppy, guppy.queue.pop(0)))
        elif delivered:
            guppy.phase = Phase.RETURNING
            guppy.legs += 1
            guppy.dest = home_point(guppy.team)
            actions.append(Dispatch(guppy, guppy.dest))
        else:
            guppy.phase = Phase.HOME
        return actions

    def _start(self, guppy: Guppy, job: Job) -> Dispatch:
        guppy.job = job
        guppy.phase = Phase.OUTBOUND
        guppy.legs += 1
        guppy.dest = job.destination
        return Dispatch(guppy, guppy.dest)
