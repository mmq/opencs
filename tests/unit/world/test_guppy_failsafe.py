"""The operator's failsafe for a stranded guppy: warp it home, reset every count (M12; I §9.4.1, II
§14, our leg-report rules; see I §9.4.1)."""

from __future__ import annotations

from fixtures.cities import seat
from fixtures.delivery import deliveries, to
from fixtures.pods import report
from opencs import policy
from opencs.messages import ServerMessage, WirePos, WireXZ, client, server
from opencs.world.city import City

HOME = WireXZ(0, 0, 300, 300)
GUPPY = 250


def point(n: int) -> WirePos:
    return WirePos(2, 2, 100 + n, 20, 200 + n)


def dest(n: int) -> WireXZ:
    return WireXZ(2, 2, 100 + n, 200 + n)


def playing() -> tuple[City, int, int]:
    c, (a, b) = seat([0, 0])
    for pid in (a, b):
        c.client_message(pid, report(0), 1.0)
    return c, a, b


def order(c: City, pid: int, n: int) -> list[tuple[int, ServerMessage]]:
    return deliveries(c, c.client_message(pid, client.TowerOrder(point(n), 0, 0), 5.0))


def arrive(c: City, pid: int) -> list[tuple[int, ServerMessage]]:
    return deliveries(c, c.client_message(pid, client.LegArrived(GUPPY), 6.0))


def kinds(out: list[tuple[int, ServerMessage]], pid: int, kind: type) -> list:  # type: ignore[type-arg]
    return [m for m in to(out, pid) if isinstance(m, kind)]


def stranded() -> tuple[City, int, int]:
    """Two orders made; both clients report the create leg; the guppy flies to the first point and
    no client ever reports that leg (a cheater reported it early and stopped, our leg-report
    rules)."""
    c, a, b = playing()
    order(c, a, 1)
    order(c, a, 2)
    arrive(c, a)
    arrive(c, b)
    return c, a, b


def test_a_warp_sends_l_home_to_every_holder_and_nobody_else() -> None:
    c, a, b = stranded()
    outsider = c.join("LATE", 1, 7.0)  # not yet introduced to the guppy
    assert not isinstance(outsider, type(None))
    out = deliveries(c, c.guppy_home(0, 8.0) or [])
    assert [(pid, m) for pid, m in out if isinstance(m, server.MobileWarp)] == [
        (a, server.MobileWarp(GUPPY, HOME)),
        (b, server.MobileWarp(GUPPY, HOME)),
    ]
    assert len(out) == 2  # and nothing else, to nobody else


def test_after_the_warp_the_clients_arrival_reports_restart_the_work_in_order() -> None:
    c, a, b = stranded()
    c.guppy_home(0, 8.0)
    # Each client reports the arrival of its warp at once. The first acts: the interrupted job
    # (tower 1, first in the queue) is flown out again; the other client's copy is held back
    # until it too has reported.
    out = arrive(c, a)
    assert kinds(out, a, server.MobileCreate) == [server.MobileCreate(GUPPY, dest(1))]
    assert kinds(out, b, server.MobileCreate) == []
    assert kinds(arrive(c, b), b, server.MobileCreate) == [server.MobileCreate(GUPPY, dest(1))]
    # Tower 1 is delivered on the next leg, then tower 2 is flown, in order, as normal.
    out = arrive(c, a)
    assert kinds(out, a, server.SpawnTower) == [
        server.SpawnTower(0, policy.STRUCTURE_IDS[0], 0, point(1), 0)
    ]
    assert kinds(out, a, server.MobileCreate) == [server.MobileCreate(GUPPY, dest(2))]
    assert c.orders.logistics.ignored_reports == 0


def test_a_guppy_that_never_existed_is_not_warped() -> None:
    c, _, _ = playing()
    assert c.guppy_home(0, 8.0) is None
    assert c.guppy_home(3, 8.0) is None


def test_a_guppy_nobody_holds_is_reset_and_says_so() -> None:
    c, a, _b = playing()
    order(c, a, 1)
    for p in c.players():
        p.view.legs.clear()  # no client holds a copy of the guppy
    assert c.guppy_home(0, 8.0) == []  # it exists and was reset, with no client to tell


def test_other_teams_guppies_are_untouched() -> None:
    c, (a, b) = seat([0, 1])
    for pid in (a, b):
        c.client_message(pid, report(0), 1.0)
    c.client_message(b, client.TowerOrder(point(1), 0, 0), 5.0)
    assert c.guppy_home(0, 8.0) is None  # team 0 has no guppy
    assert c.orders.logistics.guppy(1) is not None
