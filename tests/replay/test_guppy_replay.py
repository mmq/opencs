"""Real tower orders flown by a modeled client guppy, with a late joiner (II §14, I §7.3).

A guppy was once stranded: an order made while it flew home re-targeted it, the abandoned leg
was never reported, and every later report was matched to the wrong leg. Fake clients passed because
they answer every ``'K'``, which a real client does not. So the *orders* here are real (the times
and points of every ``'M'`` in the captures) and the clients' guppies are modeled on what was
observed with real clients:

* a client's guppy flies toward the *latest* destination it was sent at ``SPEED`` (1200 ft in
  20.3 s);
* it reports arrival at once when it is already there, and at once for a create;
* a destination replaced before it is reached is never reported.

Two clients with different latencies (0.4 s and 0.9 s, each way) share the city. Every accepted
order must be delivered to everyone and no arrival report may ever be ignored.

Late-joiner pass: a third client joins part-way (so it meets the guppy home, outbound and
returning), the two originals leave 15 s later, and every later order is made by the joiner and
must still be delivered by the joiner's reports alone.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pytest

from fixtures.captures import capture_names, client_messages
from fixtures.cities import seat
from fixtures.delivery import deliveries
from fixtures.pods import report
from opencs.clock import ManualClock
from opencs.messages import ServerMessage, client, server
from opencs.world.logistics import in_enemy_no_drop_zone

SPEED = 1200 / 20.3  # ft/s, observed
LATENCIES = (0.4, 0.9)  # per original client, seconds each way (RTT medians seen ~0.8-0.9 s)
JOINER_LATENCY = 0.6
STEP = 0.1
#: A quiet gap between two orders longer than this changes nothing (the guppy is home and idle), so
#: it is squeezed to keep the replay short.
MAX_GAP = 40.0
SETTLE = 600.0  # seconds to keep running after the last order before declaring a stall
LEAVE_AFTER = 15.0

Order = tuple[float, client.TowerOrder]


@dataclass
class SimGuppy:
    x: float
    z: float
    dx: float
    dz: float
    reported: bool = False


@dataclass
class SimClient:
    """One real client's view of the guppies it has been sent, and its reports on their way."""

    pid: int
    latency: float
    inbox: list[tuple[float, server.MobileCreate]] = field(default_factory=list)
    guppies: dict[int, SimGuppy] = field(default_factory=dict)
    outbox: list[tuple[float, int]] = field(default_factory=list)
    towers: set[int] = field(default_factory=set)
    #: How fast this client's simulation runs relative to the others' (frame rate, emulator load).
    speed: float = 1.0

    def receive(self, t: float, message: server.MobileCreate) -> None:
        self.inbox.append((t + self.latency, message))

    def step(self, t: float) -> list[int]:
        """Advance one tick; return the guppies whose leg this client's copy has just completed."""
        for item in [i for i in self.inbox if i[0] <= t]:
            self.inbox.remove(item)
            message = item[1]
            x, z = float(message.pos.world_x), float(message.pos.world_z)
            if message.id not in self.guppies:  # a new id creates it, at the point
                self.guppies[message.id] = SimGuppy(x, z, x, z)
            else:  # a known id sends it there; a leg replaced before it ended is never reported
                g = self.guppies[message.id]
                g.dx, g.dz, g.reported = x, z, False
        reports = []
        for gid, g in self.guppies.items():
            distance = math.hypot(g.dx - g.x, g.dz - g.z)
            move = SPEED * self.speed * STEP
            if distance <= move:
                g.x, g.z = g.dx, g.dz
                if not g.reported:
                    g.reported = True
                    reports.append(gid)
            else:
                g.x += (g.dx - g.x) * move / distance
                g.z += (g.dz - g.z) * move / distance
        return reports


@dataclass
class Outcome:
    accepted: int = 0
    refused: int = 0
    towers: list[int] = field(default_factory=list)
    ignored: int = 0
    stalled: bool = False
    held_by_joiner: bool = True
    #: Orders the joiner alone made (and had delivered) after the originals left.
    by_joiner: int = 0


def orders_of(name: str) -> list[Order]:
    """Every tower order of one capture, with its time, gaps squeezed, starting at t = 1."""
    found = [
        (t, m)
        for t, m in client_messages(name)
        if isinstance(m, client.TowerOrder) and not in_enemy_no_drop_zone(0, m.pos)
    ]
    out: list[Order] = []
    clock = 1.0
    previous = found[0][0] if found else 0.0
    for t, m in found:
        clock += min(t - previous, MAX_GAP)
        previous = t
        out.append((clock, m))
    return out


STREAMS = {n: orders_of(n) for n in capture_names()}
STREAMS = {n: s for n, s in STREAMS.items() if s}


def all_orders(spacing: float) -> list[Order]:
    """Every captured tower order, one every ``spacing`` seconds."""
    every = [
        m
        for n in capture_names()
        for _, m in client_messages(n)
        if isinstance(m, client.TowerOrder)
    ]
    return [(1.0 + i * spacing, m) for i, m in enumerate(every)]


class Replay:
    """The city, its modeled clients and the orders still to be made."""

    def __init__(
        self,
        stream: list[Order],
        latencies: tuple[float, float] = LATENCIES,
        speeds: tuple[float, float] = (1.0, 1.0),
    ) -> None:
        self.stream = stream
        self.pending = list(stream)
        self.clock = ManualClock()
        self.city, (self.a, self.b) = seat([0, 1], reported=False)
        for pid in (self.a, self.b):
            self.city.client_message(pid, report(4), 0.0)
        self.clients = {
            self.a: SimClient(self.a, latencies[0], speed=speeds[0]),
            self.b: SimClient(self.b, latencies[1], speed=speeds[1]),
        }
        self.orderer = self.a
        self.joiner: int | None = None
        self.leave_at: float | None = None
        self.outcome = Outcome()
        #: Reports on their way to the server: (due, client id, guppy id).
        self.in_flight: list[tuple[float, int, int]] = []

    def route(self, out: list[tuple[int, ServerMessage]]) -> None:
        """What the city sent: guppy messages to the modeled clients, tallies for the rest."""
        for pid, m in out:
            sim = self.clients.get(pid)
            if isinstance(m, server.MobileCreate) and sim is not None:
                sim.receive(self.clock.now(), m)
            elif isinstance(m, server.SpawnTower):
                if m.id not in self.outcome.towers:
                    self.outcome.towers.append(m.id)
                if sim is not None:
                    sim.towers.add(m.id)
            elif isinstance(m, server.OrderReply) and pid == self.orderer:
                if m.result == 0:
                    self.outcome.accepted += 1
                    self.outcome.by_joiner += self.orderer == self.joiner
                else:
                    self.outcome.refused += 1

    def join(self, t: float) -> None:
        r = self.city.join("LATE", 0, t)
        assert hasattr(r, "player_id")
        self.joiner = r.player_id  # type: ignore[union-attr]
        self.clients[self.joiner] = SimClient(self.joiner, JOINER_LATENCY)
        self.route(deliveries(self.city, self.city.client_message(self.joiner, report(4), t)))
        self.leave_at = t + LEAVE_AFTER

    def originals_leave(self, t: float) -> None:
        """The two original clients go; from now on the joiner reports and orders alone."""
        assert self.joiner is not None
        for gone in (self.a, self.b):
            self.city.leave(gone, t)
            del self.clients[gone]
        self.orderer = self.joiner
        self.in_flight = [r for r in self.in_flight if r[1] == self.joiner]
        self.leave_at = None

    def tick(self, t: float) -> None:
        while self.pending and self.pending[0][0] <= t:
            order = self.pending.pop(0)[1]
            self.route(deliveries(self.city, self.city.client_message(self.orderer, order, t)))
        for pid, sim in list(self.clients.items()):
            self.in_flight += [(t + sim.latency, pid, gid) for gid in sim.step(t)]
        for due in [r for r in self.in_flight if r[0] <= t]:
            self.in_flight.remove(due)
            if due[1] in self.clients:
                arrived = client.LegArrived(due[2])
                self.route(deliveries(self.city, self.city.client_message(due[1], arrived, t)))

    def done(self, t: float) -> bool:
        """Everything accepted is delivered and the guppy has had time to get home."""
        delivered = len(self.outcome.towers) == self.outcome.accepted
        return not self.pending and delivered and not self.in_flight and t > self.stream[-1][0] + 60


def run(stream: list[Order], *, join_at: float | None = None) -> Outcome:
    replay = Replay(stream)
    end = stream[-1][0] + SETTLE
    while replay.clock.now() < end and not replay.done(replay.clock.now()):
        t = replay.clock.now()
        if join_at is not None and t >= join_at and replay.joiner is None:
            replay.join(t)
        if replay.leave_at is not None and t >= replay.leave_at:
            replay.originals_leave(t)
        replay.tick(t)
        replay.clock.advance(STEP)
    outcome = replay.outcome
    outcome.ignored = replay.city.orders.logistics.ignored_reports
    outcome.stalled = len(outcome.towers) != outcome.accepted
    if replay.joiner is not None:
        view = replay.city.player(replay.joiner).view  # type: ignore[union-attr]
        outcome.held_by_joiner = all(view.holds(tid) for tid in outcome.towers)
    return outcome


def assert_clean(o: Outcome, label: str) -> None:
    assert o.ignored == 0, f"{label}: {o.ignored} arrival report(s) ignored"
    assert not o.stalled, f"{label}: {len(o.towers)} of {o.accepted} accepted orders delivered"
    assert o.accepted > 0, f"{label}: no order was accepted"


@pytest.mark.parametrize("name", sorted(STREAMS))
def test_every_captured_order_stream_is_delivered_with_no_report_ignored(name: str) -> None:
    o = run(STREAMS[name])
    assert_clean(o, name)


def test_the_dense_stream_delivers_everything_it_accepts_and_refuses_the_rest() -> None:
    o = run(all_orders(2.0))
    assert_clean(o, "dense")
    assert o.refused > 0  # 45 orders in 90 s overflow a queue of four: the 'O' 2 path ran


def test_the_spread_stream_waits_out_each_trip() -> None:
    o = run(all_orders(25.0)[:14])
    assert_clean(o, "spread")
    assert o.refused == 0


def spans(stream: list[Order]) -> float:
    return stream[-1][0] - stream[0][0]


@pytest.mark.parametrize("name", sorted(STREAMS))
def test_a_late_joiner_alone_carries_every_order_after_the_originals_leave(name: str) -> None:
    stream = STREAMS[name]
    # Join at fractions of the stream and at fixed offsets from its first order, so the joiner
    # meets the guppy home, outbound and returning.
    joins = {stream[0][0] + 0.5 + 1.7 * k for k in range(40)} | {
        stream[0][0] + spans(stream) * k / 8 for k in range(8)
    }
    for join in sorted(joins):
        assert_clean(run(stream, join_at=join), f"{name} joined at {join:.1f}")


def test_late_joiners_in_a_dense_stream_meet_the_guppy_in_every_phase() -> None:
    stream = all_orders(3.0)
    by_joiner = 0
    for k in range(60):
        join = 1.0 + 2.3 * k
        o = run(stream, join_at=join)
        assert_clean(o, f"dense joined at {join:.1f}")
        assert o.held_by_joiner, f"joined at {join:.1f}: the joiner lacks a tower"
        by_joiner += o.by_joiner
    assert by_joiner > 200  # the joiner really did carry orders: it was not idle after the leave


# --- clients that fly at different speeds -----------------------------------------------------
#
# Two clients whose simulations differ by a few percent (two emulators on one machine) with round
# trips much shorter than the difference in their arrival times. The first report of a leg acts at
# once, so the next 'K' would reach the slower client before its own copy has reported the leg it
# is flying; that leg is then never reported (I §9.4.1) and, once the faster client leaves, nothing
# can move the guppy. The slower client stays, orders, and must have every order delivered.

SPEED_FACTORS = [(1.0, 0.8), (0.8, 1.0), (1.0, 0.97), (1.03, 0.97), (0.99, 1.0), (1.2, 1.0)]
NEAR_ZERO = [(0.0, 0.0), (0.05, 0.05), (0.01, 0.1), (0.4, 0.9), (0.9, 0.4)]


def run_at_speeds(
    latencies: tuple[float, float], speeds: tuple[float, float], leave_faster_at: float | None
) -> Outcome:
    stream = all_orders(3.0)[:20]
    replay = Replay(stream, latencies, speeds)
    faster, slower = (replay.a, replay.b) if speeds[0] >= speeds[1] else (replay.b, replay.a)
    replay.orderer = slower
    end = stream[-1][0] + SETTLE
    while replay.clock.now() < end and not replay.done(replay.clock.now()):
        t = replay.clock.now()
        if leave_faster_at is not None and t >= leave_faster_at and faster in replay.clients:
            replay.city.leave(faster, t)
            del replay.clients[faster]
            replay.in_flight = [r for r in replay.in_flight if r[1] != faster]
        replay.tick(t)
        replay.clock.advance(STEP)
    outcome = replay.outcome
    outcome.ignored = replay.city.orders.logistics.ignored_reports
    outcome.stalled = len(outcome.towers) != outcome.accepted
    return outcome


@pytest.mark.parametrize("leaves", [None, 20.0], ids=["stay", "faster leaves"])
@pytest.mark.parametrize("latencies", NEAR_ZERO, ids=lambda x: f"lat={x[0]:g}/{x[1]:g}")
@pytest.mark.parametrize("speeds", SPEED_FACTORS, ids=lambda x: f"speed={x[0]:g}/{x[1]:g}")
def test_the_guppy_delivers_when_the_clients_fly_at_different_speeds(
    speeds: tuple[float, float], latencies: tuple[float, float], leaves: float | None
) -> None:
    o = run_at_speeds(latencies, speeds, leaves)
    assert_clean(o, f"speeds {speeds} latencies {latencies} leaving {leaves}")
