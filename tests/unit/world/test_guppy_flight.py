"""The guppy's flight: legs counted per client, the first report of a leg acts (I §9.4.1)."""

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
    """Order points inside tile (2, 2), distinct by ``n``."""
    return WirePos(2, 2, 100 + n, 20, 200 + n)


def dest(n: int) -> WireXZ:
    return WireXZ(2, 2, 100 + n, 200 + n)


def playing(teams: list[int]) -> tuple[City, list[int]]:
    c, ids = seat(teams)
    for pid in ids:
        c.client_message(pid, report(0), 1.0)
    return c, ids


def order(c: City, pid: int, n: int, *, mega: int = 0, priority: int = 0, now: float = 5.0) -> list:  # type: ignore[type-arg]
    return deliveries(c, c.client_message(pid, client.TowerOrder(point(n), mega, priority), now))


def arrive(c: City, pid: int, now: float = 6.0) -> list[tuple[int, ServerMessage]]:
    return deliveries(c, c.client_message(pid, client.LegArrived(GUPPY), now))


def sent_to(out: list[tuple[int, ServerMessage]], pid: int, kind: type) -> list:  # type: ignore[type-arg]
    return [m for m in to(out, pid) if isinstance(m, kind)]


def test_the_whole_trip_create_outbound_deliver_home() -> None:
    c, (a, b) = playing([0, 1])
    out = order(c, a, 1)
    assert sent_to(out, a, server.MobileCreate) == [server.MobileCreate(GUPPY, HOME)]
    # Leg 0 (the create) reported: the guppy is sent to the order point, for both clients.
    out = arrive(c, a)
    assert sent_to(out, a, server.MobileCreate) == [server.MobileCreate(GUPPY, dest(1))]
    assert sent_to(out, b, server.MobileCreate) == [server.MobileCreate(GUPPY, dest(1))]
    # Leg 1 (arrival at the point): the tower is delivered to both, the guppy is sent home.
    out = arrive(c, a)
    tower = server.SpawnTower(0, policy.STRUCTURE_IDS[0], 0, point(1), 0)
    assert sent_to(out, a, server.SpawnTower) == [tower]
    assert sent_to(out, b, server.SpawnTower) == [tower]
    assert sent_to(out, b, server.MobileCreate) == [server.MobileCreate(GUPPY, HOME)]
    # Leg 2 (home): idle; nothing is sent.
    assert arrive(c, a) == []


def test_the_delivered_tower_carries_the_orders_team_flag_and_point() -> None:
    c, (a, b) = playing([0, 1])
    order(c, b, 3, mega=1)  # blue orders: guppy 251, home at the blue corner
    out = deliveries(c, c.client_message(b, client.LegArrived(251), 6.0))
    assert sent_to(out, b, server.MobileCreate) == [server.MobileCreate(251, dest(3))]
    out = deliveries(c, c.client_message(b, client.LegArrived(251), 7.0))
    assert sent_to(out, a, server.SpawnTower) == [
        server.SpawnTower(0, policy.STRUCTURE_IDS[0], 1, point(3), 1)
    ]


def test_duplicate_reports_of_a_leg_do_nothing_and_each_client_counts_its_own_legs() -> None:
    c, (a, b) = playing([0, 1])
    order(c, a, 1)
    assert arrive(c, a)  # A: leg 0 -> dispatch
    assert arrive(c, b) == []  # B: leg 0 again, a duplicate
    assert arrive(c, b)  # B: leg 1 comes first from B -> acts: the tower is delivered
    assert arrive(c, a) == []  # A: leg 1, now a duplicate
    assert arrive(c, a) == []  # leg 2, home: acts, but the guppy is idle so nothing is sent
    assert arrive(c, b) == []  # B's leg 2: a duplicate
    assert c.orders.logistics.ignored_reports == 0


def test_a_leg_acts_on_whichever_client_reports_it_first() -> None:
    c, (a, b) = playing([0, 1])
    order(c, a, 1)
    arrive(c, b)  # B first for leg 0
    out = arrive(c, b)  # B first for leg 1
    assert sent_to(out, a, server.SpawnTower)
    arrive(c, a)
    arrive(c, a)
    assert c.orders.logistics.ignored_reports == 0


def test_an_order_made_while_the_guppy_flies_home_waits_for_the_home_report() -> None:
    c, (a, _b) = playing([0, 1])
    order(c, a, 1)
    arrive(c, a)
    arrive(c, a)  # delivered, now RETURNING
    out = order(c, a, 2)
    assert sent_to(out, a, server.MobileCreate) == []  # not redirected mid-leg
    out = arrive(c, a)  # home reported
    assert sent_to(out, a, server.MobileCreate) == [server.MobileCreate(GUPPY, dest(2))]
    assert c.orders.logistics.ignored_reports == 0


def test_a_waiting_order_is_started_straight_after_a_delivery_without_a_trip_home() -> None:
    c, (a, _) = playing([0, 1])
    order(c, a, 1)
    order(c, a, 2)
    arrive(c, a)  # create reported -> first job
    out = arrive(c, a)  # delivered -> the second job at once
    assert sent_to(out, a, server.SpawnTower)
    assert sent_to(out, a, server.MobileCreate) == [server.MobileCreate(GUPPY, dest(2))]


def flown_destinations(c: City, pid: int, reports: int) -> list[WireXZ]:
    """The non-home destinations the guppy is sent to over ``reports`` reports by one client."""
    flown: list[WireXZ] = []
    for _ in range(reports):
        for m in to(arrive(c, pid), pid):
            if isinstance(m, server.MobileCreate) and m.pos != HOME:
                flown.append(m.pos)
    return flown


def test_a_priority_order_is_flown_ahead_of_waiting_ones_but_not_the_one_in_flight() -> None:
    c, (a, _) = playing([0, 1])
    order(c, a, 1)
    arrive(c, a)  # the create is reported: job 1 is in flight
    for n in (2, 3):
        order(c, a, n)
    order(c, a, 4, priority=1)
    assert flown_destinations(c, a, 4) == [dest(4), dest(2), dest(3)]


def test_a_priority_order_made_before_the_create_is_reported_goes_first() -> None:
    c, (a, _) = playing([0, 1])
    order(c, a, 1)
    order(c, a, 2, priority=1)  # nothing is in flight yet, so nothing is displaced
    assert flown_destinations(c, a, 3) == [dest(2), dest(1)]


def test_a_client_that_leaves_stops_counting_and_the_others_drive_the_guppy() -> None:
    c, (a, b) = playing([0, 1])
    order(c, a, 1)
    arrive(c, a)
    arrive(c, b)  # B's leg 0 (duplicate)
    c.leave(a, 7.0)
    out = arrive(c, b)  # B's leg 1: nobody else has reported it, so it acts
    assert sent_to(out, b, server.SpawnTower)


def test_a_report_for_a_leg_ahead_of_the_one_in_force_is_ignored_and_counted() -> None:
    c, (a, _) = playing([0, 1])
    order(c, a, 1)
    arrive(c, a)
    arrive(c, a)  # delivered
    arrive(c, a)  # leg 2 home
    assert arrive(c, a) == []  # a fourth report: leg 3 does not exist
    assert c.orders.logistics.ignored_reports == 1


def test_a_report_for_a_guppy_the_client_does_not_hold_or_a_muncher_is_ignored() -> None:
    c, (a, _) = playing([0, 1])
    order(c, a, 1)
    assert c.client_message(a, client.LegArrived(251), 6.0) == []
    assert c.client_message(a, client.LegArrived(300), 6.0) == []
    assert c.client_message(a, client.LegArrived(5), 6.0) == []


def test_an_empty_city_is_reset_and_the_next_order_creates_the_guppy_afresh() -> None:
    c, (a, b) = playing([0, 1])
    order(c, a, 1)
    c.leave(a, 7.0)
    c.leave(b, 7.0)
    r = c.join("NEW", 0, 8.0)
    assert not isinstance(r, str) and hasattr(r, "player_id")
    pid = r.player_id  # type: ignore[union-attr]
    c.client_message(pid, report(4), 9.0)
    out = deliveries(c, c.client_message(pid, client.TowerOrder(point(1), 0, 0), 10.0))
    assert sent_to(out, pid, server.MobileCreate) == [server.MobileCreate(GUPPY, HOME)]


def test_a_reset_forgets_the_towers_so_they_feed_no_territory_income() -> None:
    c, (a, b) = playing([0, 1])
    order(c, a, 1)
    arrive(c, a)
    arrive(c, a)  # delivered
    assert c.orders.towers  # a tower stands
    c.leave(a, 7.0)
    c.leave(b, 7.0)  # the city empties and is reset
    assert c.orders.towers == {} and c.orders.territory_income() == {}
