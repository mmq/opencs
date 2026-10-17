"""Free Munchers: one every ``muncher_every`` seconds from the nearest city edge at a tower of the
team controlling most blocks (I §9.5.2, II §16.2, II §16.2)."""

from __future__ import annotations

import random

from fixtures.delivery import deliveries, to
from fixtures.lobbies import city as config_for
from fixtures.munchers import FIRST, arrive, call, destroys, ks, latches, tick, tower, xz
from fixtures.pods import report
from opencs.messages import server
from opencs.world.city import City
from opencs.world.npcs.munchers import nearest_edge

EVERY = 600.0


def world(seed: int = 1, every: float = EVERY) -> tuple[City, int, int]:
    """A city with two reported players (game begun at 0.0) and free Munchers every ``every``."""
    config = config_for("A", muncher_every=every, max_module_level=5)
    c = City(config, random.Random(seed))
    ids = []
    for team in (0, 1):
        r = c.join(f"P{team}", team, 0.0)
        assert hasattr(r, "player_id")
        ids.append(r.player_id)  # type: ignore[union-attr]
        c.client_message(ids[-1], report(4), 0.5)
    return c, ids[0], ids[1]


def target_of(c: City) -> int | None:
    (m,) = [m for m in c.munchers.alive.values() if m.free]
    return m.target


# --- timing -------------------------------------------------------------------------------------


def test_nothing_is_sent_before_the_first_period_is_up() -> None:
    c, _, _ = world()
    tower(c, 1500, 1800, team=1)
    assert tick(c, EVERY - 0.1) == []
    assert not c.munchers.alive


def test_a_free_muncher_arrives_from_the_nearest_edge_for_the_leading_teams_tower() -> None:
    c, a, b = world()
    t = tower(c, 900, 2000, team=1)
    out = tick(c, EVERY)
    edge = nearest_edge(900, 2000)
    assert edge == (50, 2000)
    for pid in (a, b):
        assert ks(out, pid) == [xz(*edge), xz(900, 2000)]  # created at the edge, sent at the tower
    assert target_of(c) == t


def test_without_a_game_there_is_no_clock_and_a_city_without_the_setting_sends_none() -> None:
    c, _, _ = world(every=0)
    tower(c, 900, 2000, team=1)
    assert tick(c, 10_000.0) == []
    assert not c.munchers.alive


def test_the_clock_restarts_when_the_city_empties() -> None:
    c, a, b = world()
    tower(c, 900, 2000, team=1)
    c.leave(a, 100.0)
    c.leave(b, 101.0)
    r = c.join("NEW", 0, 5000.0)
    assert hasattr(r, "player_id")
    c.client_message(r.player_id, report(4), 5000.5)  # type: ignore[union-attr]
    t = tower(c, 1000, 1000, team=1, now=5001.0)
    assert tick(c, 5000.0 + EVERY - 0.1) == [] and not c.munchers.alive
    tick(c, 5000.0 + EVERY)
    assert target_of(c) == t


# --- the target ---------------------------------------------------------------------------------


def test_it_goes_for_a_tower_of_the_team_with_the_most_controlled_blocks() -> None:
    for seed in range(20):
        c, _, _ = world(seed)
        mine = {tower(c, 1000, 1000, team=1), tower(c, 1900, 1000, team=1)}  # blocks (1,1), (3,1)
        tower(c, 3000, 3000, team=2)  # one block
        tick(c, EVERY)
        assert target_of(c) in mine


def test_a_tie_for_most_blocks_is_broken_at_random_but_the_same_seed_the_same_way() -> None:
    chosen = set()
    for seed in range(40):
        c, _, _ = world(seed)
        t1 = tower(c, 1000, 1000, team=1)
        t2 = tower(c, 3000, 3000, team=2)
        tick(c, EVERY)
        chosen.add(target_of(c))
        assert target_of(c) in (t1, t2)
    assert len(chosen) == 2

    def run(seed: int) -> int | None:
        c, _, _ = world(seed)
        tower(c, 1000, 1000, team=1)
        tower(c, 3000, 3000, team=2)
        tick(c, EVERY)
        return target_of(c)

    assert run(7) == run(7)


def test_the_target_among_the_leaders_blocks_is_random() -> None:
    chosen = set()
    for seed in range(40):
        c, _, _ = world(seed)
        a1 = tower(c, 1000, 1000, team=1)
        a2 = tower(c, 1900, 1000, team=1)
        tick(c, EVERY)
        chosen.add(target_of(c))
        assert target_of(c) in (a1, a2)
    assert len(chosen) == 2


def test_a_block_two_teams_have_towers_in_counts_for_nobody() -> None:
    c, _, _ = world()
    tower(c, 1000, 1000, team=1)
    tower(c, 1100, 1100, team=2)  # same block: disputed
    lone = tower(c, 3000, 3000, team=3)
    tick(c, EVERY)
    assert target_of(c) == lone


def test_no_towers_means_none_sent_and_the_next_period_tries_again() -> None:
    c, _, _ = world()
    assert tick(c, EVERY) == [] and not c.munchers.alive
    t = tower(c, 1000, 1000, team=1)
    assert tick(c, 2 * EVERY - 0.1) == []
    tick(c, 2 * EVERY)
    assert target_of(c) == t


def test_towers_only_in_disputed_blocks_send_none() -> None:
    c, _, _ = world()
    tower(c, 1000, 1000, team=1)
    tower(c, 1100, 1100, team=2)
    assert tick(c, EVERY) == [] and not c.munchers.alive


def test_an_immunized_tower_is_not_the_target_and_all_immune_sends_none() -> None:
    c, _, _ = world()
    a1 = tower(c, 1000, 1000, team=1)
    a2 = tower(c, 1900, 1000, team=1)
    c.orders.towers[a1].immunize(EVERY - 1)
    tick(c, EVERY)
    assert target_of(c) == a2
    c2, _, _ = world()
    only = tower(c2, 1000, 1000, team=1)
    c2.orders.towers[only].immunize(EVERY - 1)
    assert tick(c2, EVERY) == [] and not c2.munchers.alive


# --- one at a time, and then an ordinary Muncher -----------------------------------------------


def test_only_one_free_muncher_is_out_at_a_time() -> None:
    c, _, _ = world()
    tower(c, 1000, 1000, team=1)
    tick(c, EVERY)
    assert len(c.munchers.alive) == 1
    assert tick(c, 2 * EVERY) == []  # still out: nothing new
    assert len(c.munchers.alive) == 1


def test_when_it_has_gone_the_next_period_sends_another() -> None:
    c, a, _ = world()
    tower(c, 1000, 1000, team=1)
    tick(c, EVERY)
    arrive(c, a, FIRST, EVERY + 10)
    tick(c, EVERY + 25)  # eaten; nothing else in reach: it leaves
    arrive(c, a, FIRST, EVERY + 40)
    assert not c.munchers.alive
    tower(c, 3000, 3000, team=2)
    tick(c, 2 * EVERY)
    assert len(c.munchers.alive) == 1 and FIRST + 1 in c.munchers.alive


def test_a_called_muncher_does_not_count_as_the_free_one() -> None:
    c, a, _ = world()
    tower(c, 1000, 1000, team=1)
    call(c, a)  # at the caller's position, far from the tower: leaves
    assert len(c.munchers.alive) == 1
    tick(c, EVERY)
    assert len(c.munchers.alive) == 2


def test_afterward_it_latches_eats_and_leaves_like_any_other() -> None:
    c, a, b = world()
    t = tower(c, 1000, 1000, team=1)
    tick(c, EVERY)
    out = arrive(c, a, FIRST, EVERY + 12)
    assert latches(out, b) == [server.SendMuncher(FIRST, t)]
    assert tick(c, EVERY + 26.9) == []
    out = tick(c, EVERY + 27.0)
    assert destroys(out, a) == [t] and ks(out, a) == [xz(*nearest_edge(1000, 1000))]
    out = arrive(c, a, FIRST, EVERY + 40)
    assert destroys(out, b) == [FIRST]


def test_a_late_joiner_is_given_a_free_muncher_like_any_other() -> None:
    c, a, _ = world()
    tower(c, 1000, 1000, team=1)
    tick(c, EVERY)
    arrive(c, a, FIRST, EVERY + 12)  # latched: eating
    r = c.join("LATE", 3, EVERY + 14)
    assert hasattr(r, "player_id")
    got = to(deliveries(c, c.client_message(r.player_id, report(4), EVERY + 15)), r.player_id)  # type: ignore[union-attr]
    assert [m.pos for m in got if isinstance(m, server.MobileCreate)] == [xz(1000, 1000)] * 2
