"""Module purchase orders: replies, the home zone and guppy delivery (I §9.4.3, II §15)."""

from __future__ import annotations

import pytest

from fixtures.cities import seat
from fixtures.delivery import deliveries, to
from fixtures.pods import report
from opencs import policy
from opencs.messages import ServerMessage, WirePos, client, server
from opencs.world.city import City
from opencs.world.logistics import in_home_zone
from opencs.world.modules import drop_point

HIGH_SPEED, REPAIR, HARDEN, MEGA_TOWER = 1, 6, 18, 21


def stand(
    c: City, pid: int, x: int, z: int, *, y: int = 240, orient: int = 0, now: float = 1.0
) -> None:
    """Report the pod standing at world (x, z)."""
    pos = WirePos.from_world(x, y, z)
    c.client_message(
        pid,
        report(
            0,
            cell_x=pos.cell_x,
            cell_z=pos.cell_z,
            x_off=pos.x_off,
            z_off=pos.z_off,
            y=y,
            orient=orient,
        ),
        now,
    )


def playing(teams: list[int], **config: object) -> tuple[City, list[int]]:
    c, ids = seat(teams, **config)
    return c, ids


def order_out(
    c: City, pid: int, module: int, *, priority: int = 0, now: float = 5.0
) -> list[tuple[int, ServerMessage]]:
    return deliveries(c, c.client_message(pid, client.ModuleOrder(module, 1, priority), now))


def drops(out: list[tuple[int, ServerMessage]], pid: int) -> list[server.SpawnModule]:
    return [m for m in to(out, pid) if isinstance(m, server.SpawnModule)]


def replies(out: list[tuple[int, ServerMessage]], pid: int) -> list[server.ModuleReply]:
    return [m for m in to(out, pid) if isinstance(m, server.ModuleReply)]


# --- the replies ---


def test_a_stocked_module_is_accepted_and_the_reply_goes_to_the_orderer_alone() -> None:
    c, (a, b) = playing([0, 1])
    stand(c, a, 100, 100)
    stand(c, b, 4000, 100)
    out = order_out(c, a, REPAIR)
    assert replies(out, a) == [server.ModuleReply(REPAIR, policy.MODULE_ORDER_ACCEPTED)]
    assert replies(out, b) == []
    assert policy.MODULE_ORDER_ACCEPTED == 1


@pytest.mark.parametrize(
    ("module", "expected"),
    [(9, 1), (HARDEN, 1), (MEGA_TOWER, 0), (47, 0)],
)
def test_the_city_level_is_the_cut_off(module: int, expected: int) -> None:
    c, (a,) = playing([0])  # the test city stocks up to level 2
    stand(c, a, 100, 100)
    assert replies(order_out(c, a, module), a) == [server.ModuleReply(module, expected)]


def test_a_level_zero_city_stocks_nothing() -> None:
    c, (a,) = playing([0], max_module_level=0)
    stand(c, a, 100, 100)
    assert replies(order_out(c, a, HIGH_SPEED), a) == [server.ModuleReply(HIGH_SPEED, 0)]


def test_the_client_sent_level_does_not_decide() -> None:
    c, (a,) = playing([0])
    stand(c, a, 100, 100)
    # Mega Tower is level 3; claiming level 1 does not get it stocked.
    out = deliveries(c, c.client_message(a, client.ModuleOrder(MEGA_TOWER, 1, 0), 5.0))
    assert replies(out, a) == [server.ModuleReply(MEGA_TOWER, 0)]


def test_a_banned_module_is_refused_and_a_neighbor_is_not() -> None:
    c, (a,) = playing([0], banned_modules=(REPAIR,))
    stand(c, a, 100, 100)
    assert replies(order_out(c, a, REPAIR), a) == [server.ModuleReply(REPAIR, 0)]
    assert drops(order_out(c, a, REPAIR), a) == []
    assert replies(order_out(c, a, HIGH_SPEED), a) == [server.ModuleReply(HIGH_SPEED, 1)]


def test_an_order_naming_no_module_is_ignored() -> None:
    c, (a,) = playing([0])
    stand(c, a, 100, 100)
    assert order_out(c, a, 10) == []  # 10 is not an index


def test_an_order_before_the_first_report_is_ignored() -> None:
    c, (a,) = seat([0], reported=False)
    assert order_out(c, a, REPAIR) == []


def test_an_order_in_the_3x3_around_an_enemy_corner_is_refused_with_ok_0_never_an_o() -> None:
    c, (a,) = playing([0])
    stand(c, a, 4200 + 300, 300)  # tile (7, 0): blue's corner
    out = order_out(c, a, REPAIR)
    assert [m for _, m in out] == [server.ModuleReply(REPAIR, 0)]  # no 'O', nothing delivered
    stand(c, a, 3600 + 100, 300)  # tile 6: still in blue's 3x3
    assert [m for _, m in order_out(c, a, REPAIR)] == [server.ModuleReply(REPAIR, 0)]
    stand(c, a, 3599, 300)  # tile 5: outside
    assert replies(order_out(c, a, REPAIR), a) == [server.ModuleReply(REPAIR, 1)]


# --- the home zone ---


CORNERS = {0: (0, 0), 1: (7, 0), 2: (0, 7), 3: (7, 7)}


@pytest.mark.parametrize("team", range(4))
def test_home_zone_is_the_corner_tile_and_the_three_off_map_tiles_beside_it(team: int) -> None:
    cx, cz = CORNERS[team]
    off_x = -1 if cx == 0 else 8
    off_z = -1 if cz == 0 else 8
    inside = {(cx, cz), (off_x, cz), (cx, off_z), (off_x, off_z)}
    for tx in range(-2, 10):
        for tz in range(-2, 10):
            pos = WirePos.from_world(tx * 600 + 300, 20, tz * 600 + 300)
            assert in_home_zone(team, pos) == ((tx, tz) in inside), (team, tx, tz)


def test_the_zone_ends_on_the_foot() -> None:
    assert in_home_zone(0, WirePos.from_world(599, 20, 599))
    assert not in_home_zone(0, WirePos.from_world(600, 20, 0))
    assert not in_home_zone(0, WirePos.from_world(0, 20, 600))
    assert in_home_zone(1, WirePos.from_world(4200, 20, 0))
    assert not in_home_zone(1, WirePos.from_world(4199, 20, 0))


@pytest.mark.parametrize("team", range(4))
def test_a_module_ordered_at_home_drops_at_once_for_everyone_with_no_guppy(team: int) -> None:
    c, ids = playing([0, 1, 2, 3])
    a = ids[team]
    cx, cz = CORNERS[team]
    stand(c, a, cx * 600 + 300, cz * 600 + 300)
    out = order_out(c, a, REPAIR)
    assert len(replies(out, a)) == 1
    for pid in ids:
        (drop,) = drops(out, pid)
        assert (drop.id, drop.module) == (policy.STRUCTURE_IDS[0], REPAIR)
    assert not [m for _, m in out if isinstance(m, server.MobileCreate)]


def test_a_module_ordered_off_map_beside_home_also_drops_at_once() -> None:
    c, (a,) = playing([1])
    stand(c, a, 4800 + 100, -100)  # blue's off-map diagonal neighbor
    assert len(drops(order_out(c, a, REPAIR), a)) == 1


# --- the drop point ---


@pytest.mark.parametrize(
    ("orient", "dx", "dz"),
    [(0, 0, 20), (900, 20, 0), (1800, 0, -20), (2700, -20, 0), (450, 14, 14), (3599, 0, 20)],
)
def test_the_drop_is_20_ft_ahead_of_the_facing_and_100_ft_up(orient: int, dx: int, dz: int) -> None:
    start = WirePos.from_world(1000, 240, 1000)
    got = drop_point(start, orient)
    assert (got.world_x, got.y, got.world_z) == (1000 + dx, 340, 1000 + dz)


def test_the_instant_drop_uses_the_facing_and_height_at_request_time() -> None:
    c, (a,) = playing([0])
    stand(c, a, 300, 200, y=215, orient=900)
    (drop,) = drops(order_out(c, a, REPAIR), a)
    assert (drop.pos.world_x, drop.pos.y, drop.pos.world_z) == (320, 315, 200)
    assert (drop.vx, drop.vy, drop.vz) == (0, 0, 0)


# --- guppy delivery ---


def fly(
    c: City, pid: int, team: int, legs: int, now: float = 6.0
) -> list[tuple[int, ServerMessage]]:
    out: list[tuple[int, ServerMessage]] = []
    for i in range(legs):
        out += deliveries(c, c.client_message(pid, client.LegArrived(250 + team), now + i))
    return out


def test_away_from_home_a_guppy_flies_the_module_in_and_it_drops_on_arrival() -> None:
    c, (a, b) = playing([0, 1])
    stand(c, a, 1500, 1500, orient=900)
    stand(c, b, 4000, 4000)
    out = order_out(c, a, REPAIR)
    assert replies(out, a) == [server.ModuleReply(REPAIR, 1)]
    assert drops(out, a) == [] and drops(out, b) == []  # not yet
    assert [m for m in to(out, a) if isinstance(m, server.MobileCreate)]
    # The create leg is reported; the guppy is sent to the drop point.
    out = fly(c, a, 0, 1)
    assert not [m for m in to(out, b) if isinstance(m, server.MobileCreate)]  # b is held back
    out = fly(c, b, 0, 1)  # its own report is answered with the destination
    sent = [m for m in to(out, b) if isinstance(m, server.MobileCreate)]
    assert sent and (sent[0].pos.world_x, sent[0].pos.world_z) == (1520, 1500)
    assert drops(out, b) == []
    # Arrival: the module appears for both, 100 ft above the orderer's Y at the order.
    out = fly(c, a, 0, 1, now=8.0)
    for pid in (a, b):
        (drop,) = drops(out, pid)
        assert (drop.module, drop.pos.world_x, drop.pos.y, drop.pos.world_z) == (
            REPAIR,
            1520,
            340,
            1500,
        )


def test_the_point_is_fixed_when_ordered_not_when_it_arrives() -> None:
    c, (a,) = playing([0])
    stand(c, a, 1500, 1500)
    order_out(c, a, REPAIR)
    stand(c, a, 2500, 2500, y=300, now=2.0)  # walks away before the delivery
    out = fly(c, a, 0, 2)
    (drop,) = drops(out, a)
    assert (drop.pos.world_x, drop.pos.world_z, drop.pos.y) == (1500, 1520, 340)


def test_a_priority_module_goes_ahead_of_waiting_towers() -> None:
    c, (a,) = playing([0])
    stand(c, a, 1500, 1500)
    for n in range(3):
        c.client_message(a, client.TowerOrder(WirePos(2, 2, 100 + n, 20, 50), 0, 0), 5.0)
    order_out(c, a, REPAIR, priority=1)
    guppy = c.orders.logistics.guppy(0)
    assert guppy is not None
    assert guppy.queue[0].__class__.__name__ == "ModuleJob"


def test_module_orders_are_not_capped_by_the_tower_queue_limit() -> None:
    c, (a,) = playing([0])
    stand(c, a, 1500, 1500)
    for n in range(policy.MAX_WAITING_TOWER_ORDERS + 1):
        c.client_message(a, client.TowerOrder(WirePos(2, 2, 100 + n, 20, 50), 0, 0), 5.0)
    out = order_out(c, a, REPAIR)
    assert replies(out, a) == [server.ModuleReply(REPAIR, 1)]


def test_a_team_queue_at_the_safety_bound_refuses_with_ok_0() -> None:
    c, (a,) = playing([0])
    stand(c, a, 1500, 1500)
    for _ in range(policy.MAX_WAITING_JOBS + 1):  # the first starts the guppy's create
        out = order_out(c, a, REPAIR)
    assert replies(out, a) == [server.ModuleReply(REPAIR, 0)]


def test_an_over_budget_module_order_is_answered_ok_0() -> None:
    c, (a,) = playing([0])
    stand(c, a, 1500, 1500)
    out = deliveries(c, c.refuse_order(a, client.ModuleOrder(REPAIR, 1, 0)))
    assert out == [(a, server.ModuleReply(REPAIR, 0))]
    out = deliveries(c, c.refuse_order(a, client.TowerOrder(WirePos(2, 2, 1, 20, 1), 0, 0)))
    assert out == [(a, server.OrderReply(policy.ORDER_QUEUE_FULL))]


def test_an_order_after_a_death_before_the_first_report_is_ignored_not_asserted() -> None:
    # A 'J' ahead of the first 'b' leaves a pod that has reported but was never placed.
    c, (a, _) = seat([0, 1], reported=False)
    c.client_message(a, client.Died(), 0.6)
    c.client_message(a, report(1), 0.7)
    assert c.client_message(a, client.ModuleOrder(21, 1, 0), 5.0) == []
