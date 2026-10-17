"""A Muncher flown by modeled client Munchers, with a late joiner at many join times (I §7.3,
I §9.5.2).

A Muncher is driven entirely by the server and by the arrival reports, so there is no captured
client input to replay (no capture holds a Muncher trip). What is replayed is the client's behavior,
modeled on what was observed with real clients:

* a ``'K'`` for a new id creates the Muncher at the point with its destination zeroed;
* a ``'K'`` for a known id sets the destination and abandons the old leg, which is then never
  reported;
* arrival is reported once, with ``'K'``; the create is never reported;
* ``'E'``-``'C'`` latches it, with no report; ``'J'`` removes it;
* it flies at the guppy's speed (the Muncher's was not measured).

The towers and the timeline are synthetic: two towers 300 ft apart, a Muncher called 500 ft from
the nearer. Two original clients (0.4 s and 0.9 s each way) share the city; a third (0.6 s) joins at
each of many moments (the Muncher outbound, eating, between towers, leaving) and the originals
leave 10 s later, so from then on only the joiner's reports can move the Muncher. For every join
time: both towers are eaten, the Muncher leaves and is removed, and the joiner's copy gets no
``'K'`` or ``'E'``-``'C'`` before its own create.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import pytest

from fixtures.delivery import deliveries
from fixtures.lobbies import city as config_for
from fixtures.pods import report
from opencs.messages import ServerMessage, WirePos, client, server
from opencs.world.city import City
from opencs.world.events import Event

SPEED = 1200 / 20.3  # ft/s: the guppy's, the Muncher's is unmeasured
LATENCIES = (0.4, 0.9)  # the two original clients, seconds each way
JOINER_LATENCY = 0.6
STEP = 0.1
LEAVE_AFTER = 10.0
LIMIT = 400.0
CALLER = (1500, 1000)
TOWERS = ((1500, 1500), (1800, 1500))  # 500 ft from the caller and 300 ft apart
MUNCHER_ID = 260


@dataclass
class SimMuncher:
    x: float
    z: float
    dx: float = 0.0
    dz: float = 0.0
    done: bool = True  # a created Muncher reports nothing until it is dispatched


@dataclass
class SimClient:
    """One real client: what it has been sent, the Muncher it models and its reports."""

    pid: int
    latency: float
    inbox: list[tuple[float, ServerMessage]] = field(default_factory=list)
    munchers: dict[int, SimMuncher] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    #: Every Muncher message in the order this client was sent it, by the server.
    sent: list[ServerMessage] = field(default_factory=list)
    removed: set[int] = field(default_factory=set)
    expect_dispatch: int | None = None
    #: How fast this client's simulation runs relative to the others' (frame rate, emulator load).
    speed: float = 1.0

    def receive(self, t: float, message: ServerMessage) -> None:
        self.sent.append(message)
        self.inbox.append((t + self.latency, message))

    def step(self, t: float) -> list[int]:
        """Advance one tick; return the Munchers whose leg this client has just completed."""
        for due, message in [i for i in self.inbox if i[0] <= t]:
            self.inbox.remove((due, message))
            self._apply(message)
        reports = []
        for mid, m in self.munchers.items():
            if m.done:
                continue
            distance = math.hypot(m.dx - m.x, m.dz - m.z)
            reach = SPEED * self.speed * STEP
            if distance <= reach:
                m.x, m.z, m.done = m.dx, m.dz, True
                reports.append(mid)
            else:
                m.x += (m.dx - m.x) * reach / distance
                m.z += (m.dz - m.z) * reach / distance
        return reports

    def _apply(self, message: ServerMessage) -> None:
        if self.expect_dispatch is not None:
            followed = (
                isinstance(message, server.MobileCreate) and message.id == self.expect_dispatch
            )
            if not followed:
                self.errors.append(f"create of {self.expect_dispatch} not followed by a dispatch")
            self.expect_dispatch = None
        if isinstance(message, server.MobileCreate):
            x, z = float(message.pos.world_x), float(message.pos.world_z)
            if message.id not in self.munchers:  # a new id: created, destination zeroed
                if message.id in self.removed:
                    self.errors.append(f"'K' for Muncher {message.id} after its removal")
                self.munchers[message.id] = SimMuncher(x, z)
                self.expect_dispatch = message.id
            else:  # a known id: the old leg is abandoned, never reported
                m = self.munchers[message.id]
                m.dx, m.dz, m.done = x, z, False
        elif isinstance(message, server.SendMuncher):
            if message.muncher not in self.munchers:
                self.errors.append(f"'E'-'C' for Muncher {message.muncher} it never saw created")
            else:
                self.munchers[message.muncher].done = True  # latched: no report
        elif isinstance(message, server.Destroy):
            self.munchers.pop(message.id, None)
            self.removed.add(message.id)


def muncher_traffic(messages: list[ServerMessage]) -> list[ServerMessage]:
    return [
        m
        for m in messages
        if isinstance(m, (server.MobileCreate, server.SendMuncher))
        or (isinstance(m, server.Destroy) and m.id == MUNCHER_ID)
    ]


class Harness:
    """The city, its modeled clients and the wires between them."""

    def __init__(
        self,
        latencies: tuple[float, ...] = LATENCIES,
        speeds: tuple[float, ...] = (1.0, 1.0),
    ) -> None:
        self.c = City(config_for("A", max_module_level=5), random.Random(1))
        ids = []
        for team in (0, 1):
            r = self.c.join(f"P{team}", team, 0.0)
            assert hasattr(r, "player_id")
            ids.append(r.player_id)  # type: ignore[union-attr]
        caller = WirePos.from_world(CALLER[0], 20, CALLER[1])
        at = {"cell_x": caller.cell_x, "cell_z": caller.cell_z}
        at |= {"x_off": caller.x_off, "z_off": caller.z_off}
        for pid in ids:
            self.c.client_message(pid, report(0, **at), 0.5)
        for x, z in TOWERS:
            self.c.orders.place_tower(3, WirePos.from_world(x, 20, z), False, 1.0)
        self.present = [
            SimClient(pid, lat, speed=f) for pid, lat, f in zip(ids, latencies, speeds, strict=True)
        ]
        self.everyone = list(self.present)
        self.to_server: list[tuple[float, int, ServerMessage]] = []
        self.tower_ids = set(self.c.orders.towers)
        self.eaten: set[int] = set()

    def send(self, t: float, events: list[Event]) -> None:
        """Hand the Muncher messages among ``events`` to the clients they are for."""
        for pid, message in deliveries(self.c, events):
            for sim in self.present:
                if sim.pid == pid and muncher_traffic([message]):
                    sim.receive(t, message)
        self.eaten |= self.tower_ids - set(self.c.orders.towers)

    def join(self, t: float) -> None:
        r = self.c.join("LATE", 2, t)
        assert hasattr(r, "player_id")
        joiner = SimClient(r.player_id, JOINER_LATENCY)  # type: ignore[union-attr]
        self.present.append(joiner)
        self.everyone.append(joiner)
        self.send(t, self.c.client_message(joiner.pid, report(4), t))

    def originals_leave(self, t: float) -> None:
        for sim in self.present[: len(LATENCIES)]:
            self.c.leave(sim.pid, t)
        self.present = self.present[len(LATENCIES) :]

    def leave(self, sim: SimClient, t: float) -> None:
        self.c.leave(sim.pid, t)
        self.present.remove(sim)

    def step(self, t: float) -> None:
        for sim in self.present:
            for mid in sim.step(t):
                self.to_server.append((t + sim.latency, sim.pid, client.LegArrived(mid)))
        for item in [i for i in self.to_server if i[0] <= t]:
            self.to_server.remove(item)
            self.send(t, self.c.client_message(item[1], item[2], t))
        self.send(t, self.c.tick(t))

    def running(self, t: float) -> bool:
        return t < LIMIT and (
            bool(self.c.munchers.alive) or t < 2.0 or any(sim.inbox for sim in self.present)
        )

    def failures(self) -> list[str]:
        fails = [e for sim in self.everyone for e in sim.errors]
        if self.eaten != self.tower_ids:
            fails.append(f"towers eaten {sorted(self.eaten)}, expected {sorted(self.tower_ids)}")
        if self.c.munchers.alive:
            fails.append("the Muncher never left")
        if self.c.munchers.ignored_reports:
            fails.append(f"{self.c.munchers.ignored_reports} out-of-protocol reports")
        for sim in self.everyone:
            fails += first_message_problems(sim)
            if sim in self.present and sim.munchers:  # one that left keeps what it last saw
                fails.append(f"client {sim.pid} still shows a Muncher: {sorted(sim.munchers)}")
        return fails


def first_message_problems(sim: SimClient) -> list[str]:
    """The first Muncher messages a client is ever sent must be its create and its dispatch."""
    traffic = muncher_traffic(sim.sent)
    if not traffic:
        return []
    first, second = traffic[0], traffic[1]
    if (
        isinstance(first, server.MobileCreate)
        and isinstance(second, server.MobileCreate)
        and first.id == second.id
    ):
        return []
    return [f"client {sim.pid} was sent {first} before its create and dispatch"]


def run(join_at: float | None) -> list[str]:
    h = Harness()
    t = 1.0
    h.send(t, h.c.client_message(h.present[0].pid, client.EffectRequest("C"), t))
    leave_at = None if join_at is None else join_at + LEAVE_AFTER
    while h.running(t):
        if join_at is not None and t >= join_at:
            join_at = None
            h.join(t)
        if leave_at is not None and t >= leave_at:
            leave_at = None
            h.originals_leave(t)
        h.step(t)
        t += STEP
    return h.failures()


JOIN_TIMES = [None, *[float(j) for j in range(2, 100, 3)]]


@pytest.mark.parametrize(
    "join_at", JOIN_TIMES, ids=lambda j: "no joiner" if j is None else f"t={j:g}"
)
def test_the_muncher_trip_completes_for_every_join_time(join_at: float | None) -> None:
    assert run(join_at) == []


# --- clients that fly at different speeds -----------------------------------------------------
#
# Two clients whose simulations differ by a few percent (two emulators on one machine) and whose
# round trips are far shorter than the difference in their arrival times. Whichever copy arrives
# first is acted on at once, so the other copy has not reported yet when the latch or the next
# dispatch would reach it: the server must hold that client back until its own report, or the leg
# it was flying is replaced and never reported, and once the faster client leaves nothing can move
# the Muncher (I §9.4.1, I §7.3).

SPEED_FACTORS = [(1.0, 0.8), (0.8, 1.0), (1.0, 0.97), (1.03, 0.97), (0.99, 1.0), (1.2, 1.0)]
NEAR_ZERO = [(0.0, 0.0), (0.05, 0.05), (0.01, 0.1), (0.4, 0.9), (0.9, 0.4)]
FIRST_LATCH_BY = 25.0  # the faster client leaves once the first latch has gone out


def run_at_speeds(
    latencies: tuple[float, float], speeds: tuple[float, float], leave_faster_at: float | None
) -> list[str]:
    h = Harness(latencies, speeds)
    t = 1.0
    h.send(t, h.c.client_message(h.present[0].pid, client.EffectRequest("C"), t))
    faster = max(h.present, key=lambda s: s.speed)
    while h.running(t):
        if leave_faster_at is not None and t >= leave_faster_at and faster in h.present:
            h.leave(faster, t)
        h.step(t)
        t += STEP
    return h.failures()


@pytest.mark.parametrize("leaves", [None, FIRST_LATCH_BY], ids=["stay", "faster leaves"])
@pytest.mark.parametrize("latencies", NEAR_ZERO, ids=lambda x: f"lat={x[0]:g}/{x[1]:g}")
@pytest.mark.parametrize("speeds", SPEED_FACTORS, ids=lambda x: f"speed={x[0]:g}/{x[1]:g}")
def test_the_muncher_trip_completes_when_the_clients_fly_at_different_speeds(
    speeds: tuple[float, float], latencies: tuple[float, float], leaves: float | None
) -> None:
    assert run_at_speeds(latencies, speeds, leaves) == []
