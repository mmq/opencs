"""Tower orders: refusal zones, the queue and the replies (I §9.4.2, II §14, I §9.4.3, II §14)."""

from __future__ import annotations

import pytest

from fixtures.cities import seat
from fixtures.delivery import deliveries, to
from fixtures.pods import report
from opencs import policy
from opencs.messages import WirePos, WireXZ, client, server
from opencs.world.city import City
from opencs.world.logistics import (
    Create,
    Dispatch,
    Guppy,
    Logistics,
    TowerJob,
    home_point,
    in_enemy_no_drop_zone,
    tile_of,
)

CORNERS = {0: (0, 0), 1: (7, 0), 2: (0, 7), 3: (7, 7)}


def at_tile(tx: int, tz: int, x_off: int = 300, z_off: int = 300) -> WirePos:
    """A point in tile (tx, tz); negative and 8+ tiles are off-map."""
    return WirePos.from_world(tx * 600 + x_off, 20, tz * 600 + z_off)


def order(pos: WirePos, mega: int = 0, priority: int = 0) -> client.TowerOrder:
    return client.TowerOrder(pos, mega, priority)


def playing(teams: list[int]) -> tuple[City, list[int]]:
    c, ids = seat(teams)
    for pid in ids:
        c.client_message(pid, report(0), 1.0)
    return c, ids


# --- the geometry ---


def test_tiles_floor_toward_negative_infinity() -> None:
    assert tile_of(0, 599) == (0, 0)
    assert tile_of(600, 1199) == (1, 1)
    assert tile_of(-1, -600) == (-1, -1)
    assert tile_of(4800, 4799) == (8, 7)


def test_home_points_are_the_centers_of_the_corner_tiles() -> None:
    assert home_point(0) == WireXZ(0, 0, 300, 300)
    assert home_point(1) == WireXZ(7, 0, 300, 300)
    assert home_point(2) == WireXZ(0, 7, 300, 300)
    assert home_point(3) == WireXZ(7, 7, 300, 300)


@pytest.mark.parametrize("team", range(4))
@pytest.mark.parametrize("enemy", range(4))
def test_every_tile_of_an_enemy_corners_3x3_is_refused_and_its_ring_is_allowed(
    team: int, enemy: int
) -> None:
    if team == enemy:
        return
    cx, cz = CORNERS[enemy]
    others = [c for t, c in CORNERS.items() if t not in (team, enemy)]
    for tx in range(cx - 2, cx + 3):
        for tz in range(cz - 2, cz + 3):
            inside = abs(tx - cx) <= 1 and abs(tz - cz) <= 1
            near_other = any(abs(tx - ox) <= 1 and abs(tz - oz) <= 1 for ox, oz in others)
            refused = in_enemy_no_drop_zone(team, at_tile(tx, tz))
            assert refused == (inside or near_other), (team, enemy, tx, tz)


def test_the_zone_ends_on_the_foot_not_the_tile_center() -> None:
    # Red's corner (0,0): tile 1 is in its 3x3 up to world x 1199; 1200 is tile 2.
    assert in_enemy_no_drop_zone(1, WirePos.from_world(1199, 20, 300))
    assert not in_enemy_no_drop_zone(1, WirePos.from_world(1200, 20, 300))
    # Blue's corner (7,0): the 3x3 begins at tile 6 = world x 3600.
    assert not in_enemy_no_drop_zone(0, WirePos.from_world(3599, 20, 300))
    assert in_enemy_no_drop_zone(0, WirePos.from_world(3600, 20, 300))
    # Off the map beside a corner is in its 3x3: tile -1 for red, tile 8 for blue.
    assert in_enemy_no_drop_zone(1, WirePos.from_world(-1, 20, 300))
    assert in_enemy_no_drop_zone(0, WirePos.from_world(4800, 20, 300))
    assert not in_enemy_no_drop_zone(0, WirePos.from_world(5400, 20, 300))  # tile 9


def test_a_team_may_order_in_and_around_its_own_corner() -> None:
    # The 3x3s of two corners never overlap on an 8x8 map, so a team's own 3x3 is all allowed.
    for team, (cx, cz) in CORNERS.items():
        for tx in range(cx - 1, cx + 2):
            for tz in range(cz - 1, cz + 2):
                assert not in_enemy_no_drop_zone(team, at_tile(tx, tz)), (team, tx, tz)


# --- the job queue ---


def job(n: int, priority: bool = False, team: int = 0) -> TowerJob:
    return TowerJob(team, WirePos(2, 2, n, 20, n), False, priority, 0)


def test_a_first_order_creates_the_guppy_and_waits_for_the_create_to_be_reported() -> None:
    logistics = Logistics()
    actions = logistics.order(job(1))
    assert len(actions) == 1 and isinstance(actions[0], Create)
    guppy = logistics.guppy(0)
    assert guppy is not None and guppy.queue == [job(1)] and guppy.job is None
    assert guppy.entity_id == 250
    assert logistics.order(job(2)) == []  # no second create, no dispatch
    assert guppy.queue == [job(1), job(2)]


def test_priority_jobs_go_ahead_of_non_priority_and_behind_earlier_priority() -> None:
    g = Guppy(0)
    for j in (job(1), job(2, True), job(3), job(4, True), job(5)):
        g.enqueue(j)
    assert [j.pos.x_off for j in g.queue] == [2, 4, 1, 3, 5]


def test_the_guppy_is_not_dispatched_before_it_is_home() -> None:
    logistics = Logistics()
    for n in range(5):
        assert not any(isinstance(a, Dispatch) for a in logistics.order(job(n)))


def test_each_team_has_its_own_guppy_and_queue() -> None:
    logistics = Logistics()
    logistics.order(job(1, team=0))
    logistics.order(job(1, team=2))
    g0, g2 = logistics.guppy(0), logistics.guppy(2)
    assert g0 is not None and g2 is not None
    assert (g0.entity_id, g2.entity_id) == (250, 252)
    assert len(g0.queue) == len(g2.queue) == 1


def test_every_team_guppy_has_its_own_id_from_250_to_253() -> None:
    # The id sets the guppy's color and altitude on the client (I §9.4.1).
    logistics = Logistics()
    for team in range(4):
        logistics.order(job(1, team=team))
    ids = [g.entity_id for team in range(4) if (g := logistics.guppy(team)) is not None]
    assert ids == [250, 251, 252, 253]


# --- the replies ---


def test_an_accepted_order_is_answered_ok_to_the_orderer_alone() -> None:
    c, (a, b) = playing([0, 1])
    out = deliveries(c, c.client_message(a, order(at_tile(2, 2)), 5.0))
    assert to(out, a)[0] == server.OrderReply(0)
    assert server.OrderReply(0) not in to(out, b)


def test_an_order_beside_an_enemy_corner_is_refused_with_sub_1_and_queues_nothing() -> None:
    c, (a, b) = playing([0, 1])
    out = deliveries(c, c.client_message(a, order(at_tile(7, 1)), 5.0))  # blue's 3x3
    assert out == [(a, server.OrderReply(1))]
    # And the same tile for blue itself is fine.
    ok = deliveries(c, c.client_message(b, order(at_tile(7, 1)), 5.0))
    assert to(ok, b)[0] == server.OrderReply(0)


def test_the_fifth_waiting_order_is_refused_with_sub_2_and_other_teams_are_unaffected() -> None:
    c, (a, b) = playing([0, 1])
    for n in range(policy.MAX_WAITING_TOWER_ORDERS):
        out = deliveries(c, c.client_message(a, order(at_tile(2, 2, 10 + n)), 5.0 + n))
        assert to(out, a)[0] == server.OrderReply(0), n
    refused = deliveries(c, c.client_message(a, order(at_tile(2, 2, 99)), 9.0))
    assert refused == [(a, server.OrderReply(2))]
    other = deliveries(c, c.client_message(b, order(at_tile(5, 2)), 9.5))
    assert to(other, b)[0] == server.OrderReply(0)


def test_the_first_order_creates_the_guppy_for_every_playing_client_once() -> None:
    c, (a, b, d) = playing([0, 1, 0])
    out = deliveries(c, c.client_message(a, order(at_tile(2, 2)), 5.0))
    create = server.MobileCreate(250, WireXZ(0, 0, 300, 300))
    assert [m for _, m in out if isinstance(m, server.MobileCreate)] == [create] * 3
    assert {p for p, m in out if m == create} == {a, b, d}
    again = deliveries(c, c.client_message(d, order(at_tile(2, 3)), 6.0))
    assert not [m for _, m in again if isinstance(m, server.MobileCreate)]


def test_a_module_order_is_answered_with_no_stock_to_the_sender_only() -> None:
    c, (a, _b) = playing([0, 1])
    out = deliveries(c, c.client_message(a, client.ModuleOrder(21, 1, 0), 5.0))
    assert out == [(a, server.ModuleReply(21, 0))]


def test_a_module_order_naming_no_catalog_module_is_ignored() -> None:
    c, (a, _) = playing([0, 1])
    assert c.client_message(a, client.ModuleOrder(10, 1, 0), 5.0) == []  # 10 is not an index


def test_an_order_from_a_client_that_has_not_reported_is_ignored() -> None:
    c, (a, _) = seat([0, 1], reported=False)
    assert c.client_message(a, order(at_tile(2, 2)), 5.0) == []


def test_the_megatower_flag_is_exactly_one() -> None:
    c, (a, _) = playing([0, 1])
    c.client_message(a, order(at_tile(2, 2), mega=2), 5.0)
    guppy = c._logistics.guppy(0)
    assert guppy is not None and guppy.queue[0].mega is False
    c.client_message(a, order(at_tile(2, 2, 40), mega=1), 6.0)
    assert guppy.queue[1].mega is True
