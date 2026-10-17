"""A called Muncher: create and dispatch, leg counting, latch, meal, chain, departure, immunity and
durability (I §9.5.2, II §16.2, I §7.3, I §8.13, ``'E'``-``'C'``, ``'J'``)."""

from __future__ import annotations

from fixtures.cities import seat
from fixtures.delivery import to
from fixtures.munchers import (
    FIRST,
    HERE,
    arrive,
    call,
    destroys,
    hit,
    ks,
    latches,
    tick,
    tower,
    world,
    xz,
)
from fixtures.pods import report
from opencs import policy
from opencs.messages import WirePos, client, server
from opencs.world.npcs.munchers import nearest_edge

# --- the call -----------------------------------------------------------------------------------


def test_the_create_is_at_the_caller_and_is_followed_at_once_by_a_dispatch_to_the_tower() -> None:
    c, (a, b) = world()
    t = tower(c, 1500, 1800)
    out = call(c, a)
    for pid in (a, b):
        assert to(out, pid) == [
            server.MobileCreate(FIRST, xz(*HERE)),  # created where the caller stands
            server.MobileCreate(FIRST, xz(1500, 1800)),  # then sent to the tower
        ]
    assert c.munchers.alive[FIRST].target == t


def test_the_create_and_the_dispatch_reach_a_client_in_that_order_whoever_else_is_slow() -> None:
    c, (a, b) = world()
    tower(c, 1500, 1800)
    out = call(c, a)
    # Both for one client, adjacent in the one delivery list: nothing can come between them.
    for pid in (a, b):
        indices = [i for i, (p, _) in enumerate(out) if p == pid]
        assert indices == list(range(indices[0], indices[0] + 2))


def test_a_client_still_loading_is_not_sent_the_muncher_until_it_reports() -> None:
    c, (a, b) = world(count=2)
    tower(c, 1500, 1800)
    late = c.join("LATE", 3, 5.0)
    assert hasattr(late, "player_id")
    out = call(c, a)
    assert to(out, late.player_id) == []  # type: ignore[union-attr]
    assert len(ks(out, b)) == 2


def test_the_muncher_goes_for_the_nearest_tower_of_any_team_within_600_feet() -> None:
    c, (a, _) = world()
    far = tower(c, 1500, 2000, team=0)  # 500 ft away, the caller's own team
    near = tower(c, 1300, 1500, team=1)  # 200 ft away
    tower(c, 1500, 1000, team=2)  # 500 ft away
    call(c, a)
    assert c.munchers.alive[FIRST].target == near and near != far


def test_a_tower_exactly_600_feet_away_is_in_reach_and_one_foot_more_is_not() -> None:
    c, (a, _) = world()
    inside = tower(c, 1500, 2100)  # 600
    call(c, a)
    assert c.munchers.alive[FIRST].target == inside
    c2, (a2, _) = world()
    tower(c2, 1500, 2101)  # 601
    out = call(c2, a2)
    assert c2.munchers.alive[FIRST].target is None  # nothing in reach: it heads for the edge
    assert ks(out, a2)[1] == xz(*nearest_edge(1500, 1500))


def test_an_immunized_tower_is_not_a_target_but_the_next_nearest_is() -> None:
    c, (a, _) = world()
    immune = tower(c, 1300, 1500, team=0)
    other = tower(c, 1500, 1900, team=1)
    c.orders.towers[immune].immunize(5.0)
    call(c, a, now=10.0)
    assert c.munchers.alive[FIRST].target == other


def test_a_tower_is_a_target_again_when_its_immunity_has_ended() -> None:
    c, (a, _) = world()
    t = tower(c, 1300, 1500)
    c.orders.towers[t].immunize(0.0)
    call(c, a, now=policy.IMMUNIZE_SECONDS - 0.1)
    assert c.munchers.alive[FIRST].target is None
    call(c, a, now=policy.IMMUNIZE_SECONDS)
    assert c.munchers.alive[FIRST + 1].target == t


def test_the_nearest_city_edge_for_a_muncher_with_nothing_to_eat() -> None:
    assert nearest_edge(100, 2400) == (50, 2400)  # west
    assert nearest_edge(4700, 2400) == (4750, 2400)  # east
    assert nearest_edge(2400, 100) == (2400, 50)  # south (z small)
    assert nearest_edge(2400, 4700) == (2400, 4750)
    assert nearest_edge(-300, 100) == (50, 100)  # off the map: clamped first


# --- the trip -----------------------------------------------------------------------------------


def test_the_first_report_latches_it_once_and_the_create_is_never_a_report() -> None:
    c, (a, b) = world()
    t = tower(c, 1500, 1800)
    call(c, a)
    out = arrive(c, a, FIRST, 16.0)  # the first dispatch's arrival (leg 1)
    for pid in (a, b):
        assert latches(out, pid) == [server.SendMuncher(FIRST, t)]
    assert arrive(c, b, FIRST, 16.5) == []  # the other client's report of the same leg
    assert c.munchers.alive[FIRST].acted == 1


def test_a_client_cannot_report_more_legs_than_it_was_sent() -> None:
    c, (a, b) = world()
    tower(c, 1500, 1800)
    call(c, a)
    arrive(c, a, FIRST, 16.0)
    assert arrive(c, a, FIRST, 16.1) == []  # a second report of a leg it was sent once
    assert c.munchers.ignored_reports == 1
    assert arrive(c, b, FIRST, 16.2) == []  # b's own report: a duplicate, not out of protocol
    assert c.munchers.ignored_reports == 1


def test_a_report_from_a_client_that_holds_no_copy_is_ignored() -> None:
    c, (a, _) = world()
    tower(c, 1500, 1800)
    call(c, a)
    late = c.join("LATE", 3, 5.0)  # never reported: holds nothing
    assert hasattr(late, "player_id")
    assert arrive(c, late.player_id, FIRST, 16.0) == []  # type: ignore[union-attr]
    assert c.munchers.alive[FIRST].acted == 0


def test_a_report_for_an_id_that_is_not_a_muncher_or_guppy_does_nothing() -> None:
    c, (a, _) = world()
    assert arrive(c, a, FIRST + 5, 5.0) == []


def test_the_tower_is_eaten_after_exactly_15_seconds_and_goes_to_every_holder() -> None:
    c, (a, b) = world()
    t = tower(c, 1500, 1800)
    call(c, a)
    arrive(c, a, FIRST, 16.0)
    assert tick(c, 30.9) == []
    out = tick(c, 31.0)
    assert destroys(out, a) == [t] and destroys(out, b) == [t]
    assert t not in c.orders.towers


def test_after_a_meal_it_chains_to_the_next_tower_within_600_feet_of_where_it_is() -> None:
    c, (a, b) = world()
    one = tower(c, 1500, 1800)
    two = tower(c, 1500, 2300)  # 500 from the first, 800 from the caller
    call(c, a)
    arrive(c, a, FIRST, 16.0)
    out = tick(c, 31.0)
    assert ks(out, a) == [xz(1500, 2300)] and ks(out, b) == [xz(1500, 2300)]
    assert c.munchers.alive[FIRST].target == two and one not in c.orders.towers
    assert arrive(c, a, FIRST, 40.0) != []  # leg 2's arrival latches it on the second tower
    assert c.munchers.alive[FIRST].acted == 2


def test_with_nothing_left_it_flies_to_the_edge_and_is_removed_on_arrival() -> None:
    c, (a, b) = world()
    t = tower(c, 1500, 1800)
    call(c, a)
    arrive(c, a, FIRST, 16.0)
    out = tick(c, 31.0)
    edge = xz(*nearest_edge(1500, 1800))
    assert ks(out, a) == [edge]
    assert destroys(tick(c, 100.0), a) == []  # not removed on a timer
    out = arrive(c, a, FIRST, 50.0)
    assert destroys(out, a) == [FIRST] and destroys(out, b) == [FIRST]
    assert FIRST not in c.munchers.alive and t not in c.orders.towers
    # An 'H' cannot carry its id, so the removal is a 'J' and no 'H' is ever sent.
    assert not [m for _, m in out if isinstance(m, server.Delete)]


def test_a_removed_munchers_id_is_not_reported_on_again() -> None:
    c, (a, b) = world()
    call(c, a)  # no towers: straight to the edge
    arrive(c, a, FIRST, 20.0)
    assert arrive(c, b, FIRST, 20.5) == []
    assert not c.is_known_npc(FIRST)


def test_a_tower_destroyed_while_the_muncher_flies_sends_it_on_not_into_a_latch() -> None:
    c, (a, _) = world()
    first = tower(c, 1500, 1800)
    second = tower(c, 1500, 2200)
    call(c, a)
    c.orders.destroy_tower(c.orders.towers[first], 12.0)
    out = arrive(c, a, FIRST, 16.0)
    assert latches(out, a) == [] and ks(out, a) == [xz(1500, 2200)]
    assert c.munchers.alive[FIRST].target == second


def test_a_tower_immunized_during_the_meal_is_not_eaten_and_the_muncher_moves_on() -> None:
    c, (a, _) = world()
    t = tower(c, 1500, 1800)
    call(c, a)
    arrive(c, a, FIRST, 16.0)
    c.orders.towers[t].immunize(20.0)
    out = tick(c, 31.0)
    assert destroys(out, a) == [] and t in c.orders.towers
    assert ks(out, a)  # it goes on (to the edge: its only tower is immune)


def test_a_tower_immunized_before_the_arrival_is_not_latched() -> None:
    c, (a, _) = world()
    t = tower(c, 1500, 1800)
    call(c, a)
    c.orders.towers[t].immunize(12.0)
    out = arrive(c, a, FIRST, 16.0)
    assert latches(out, a) == [] and ks(out, a)


# --- durability ---------------------------------------------------------------------------------


def test_a_muncher_takes_3000_on_the_per_shot_table_and_is_removed_at_zero() -> None:
    c, (a, b) = world()
    tower(c, 1500, 1800)
    call(c, a)
    for _ in range(5):
        assert hit(c, b, FIRST) == []
    out = hit(c, b, FIRST)
    assert destroys(out, a) == [FIRST] and destroys(out, b) == [FIRST]
    assert FIRST not in c.munchers.alive and not c.munchers.alive


def test_a_destroyed_muncher_eats_nothing() -> None:
    c, (a, b) = world()
    t = tower(c, 1500, 1800)
    call(c, a)
    arrive(c, a, FIRST, 16.0)
    for _ in range(6):
        hit(c, b, FIRST, now=17.0)
    assert tick(c, 31.0) == [] and t in c.orders.towers


def test_a_weapon_class_past_the_table_does_not_hurt_it() -> None:
    c, (a, b) = world()
    call(c, a)
    hit(c, b, FIRST)
    c.client_message(b, client.HitReport(FIRST, 7), 21.0)
    assert c.munchers.alive[FIRST].hp == policy.MUNCHER_HP - 500


# --- limits and the city's life ------------------------------------------------------------------


def test_at_the_cap_a_further_call_is_ignored() -> None:
    c, (a, _) = world()
    for _ in range(policy.MAX_MUNCHERS):
        call(c, a)
    assert len(c.munchers.alive) == policy.MAX_MUNCHERS
    assert call(c, a) == [] and len(c.munchers.alive) == policy.MAX_MUNCHERS


def test_munchers_ids_come_from_260_up() -> None:
    c, (a, _) = world()
    call(c, a)
    call(c, a)
    assert sorted(c.munchers.alive) == [FIRST, FIRST + 1]


def test_an_emptied_city_forgets_its_munchers() -> None:
    c, (a, b) = world()
    call(c, a)
    c.leave(a, 30.0)
    c.leave(b, 31.0)
    assert not c.munchers.alive


def test_a_forged_call_muncher_in_a_city_that_does_not_stock_it_does_nothing() -> None:
    c, (a, _) = seat([0, 1], max_module_level=0)
    pos = WirePos.from_world(HERE[0], 20, HERE[1])
    c.client_message(a, report(0, cell_x=pos.cell_x, cell_z=pos.cell_z), 1.0)
    assert call(c, a) == []
    assert not c.munchers.alive


def test_a_slower_clients_reports_of_each_leg_never_act_a_second_time() -> None:
    c, (a, b) = world()
    tower(c, 1500, 1800)
    tower(c, 1500, 2300)
    call(c, a)
    assert latches(arrive(c, a, FIRST, 16.0), a)  # leg 1, a first
    tick(c, 31.0)  # tower one eaten, leg 2 dispatched
    assert arrive(c, b, FIRST, 31.5) == []  # b's leg-1 report: a duplicate of the one acted on
    assert latches(arrive(c, a, FIRST, 40.0), b)  # leg 2, a first again
    assert arrive(c, b, FIRST, 40.5) == []
    assert c.munchers.alive[FIRST].acted == 2
