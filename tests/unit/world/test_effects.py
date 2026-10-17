"""Effect modules: which towers each one reaches and what it does (I §9.3.5, II §16.1)."""

from __future__ import annotations

import logging

import pytest

from fixtures.cities import seat
from fixtures.delivery import deliveries, to
from fixtures.pods import report
from opencs import policy
from opencs.messages import ServerMessage, WirePos, client, server
from opencs.world.city import City

HERE = (1500, 1500)  # where the sender stands, world feet
REACH = policy.ONE_BLOCK_FEET


def at(dx: int, dz: int = 0) -> WirePos:
    return WirePos.from_world(HERE[0] + dx, 20, HERE[1] + dz)


def world(teams: list[int] | None = None, **config: object) -> tuple[City, list[int]]:
    """Players (team 0 first) all standing at HERE, in a city that stocks every effect module
    unless ``config`` says otherwise."""
    config = {"max_module_level": 4, **config}
    c, ids = seat(teams or [0, 1], **config)
    pos = at(0)
    for pid in ids:
        c.client_message(
            pid,
            report(0, cell_x=pos.cell_x, cell_z=pos.cell_z, x_off=pos.x_off, z_off=pos.z_off),
            1.0,
        )
    return c, ids


def tower(c: City, team: int, where: WirePos, mega: bool = False) -> int:
    out = deliveries(c, c.orders.place_tower(team, where, mega, 2.0))
    return next(m.id for _, m in out if isinstance(m, server.SpawnTower))


def use(c: City, pid: int, letter: str, now: float = 10.0) -> list[tuple[int, ServerMessage]]:
    return deliveries(c, c.client_message(pid, client.EffectRequest(letter), now))


def hp(c: City, tower_id: int) -> tuple[int, int]:
    t = c.orders.towers[tower_id]
    return t.hp, t.max_hp


def damage(c: City, tower_id: int, amount: int) -> None:
    c.orders.towers[tower_id].hp -= amount


# --- the reach ---


def test_reach_is_600_ft_across_the_ground_inclusive() -> None:
    c, (a, _b) = world()
    inside = tower(c, 0, at(REACH))
    outside = tower(c, 0, at(REACH + 1))
    diagonal_in = tower(c, 0, at(360, 480))  # 600 exactly
    diagonal_out = tower(c, 0, at(361, 480))
    for t in (inside, outside, diagonal_in, diagonal_out):
        damage(c, t, 1000)
    use(c, a, "R")
    full = policy.TOWER_HP
    assert hp(c, inside)[0] == full and hp(c, diagonal_in)[0] == full
    assert hp(c, outside)[0] == full - 1000 and hp(c, diagonal_out)[0] == full - 1000


def test_reach_measures_from_the_senders_last_report() -> None:
    c, (a, _b) = world()
    t = tower(c, 0, at(2000))
    damage(c, t, 500)
    use(c, a, "R")
    assert hp(c, t)[0] == policy.TOWER_HP - 500  # too far
    far = at(1800)
    c.client_message(
        a, report(0, cell_x=far.cell_x, cell_z=far.cell_z, x_off=far.x_off, z_off=far.z_off), 5.0
    )
    use(c, a, "R")
    assert hp(c, t)[0] == policy.TOWER_HP


def test_effects_from_a_client_that_has_not_reported_or_is_dead_do_nothing() -> None:
    c, (a, _b) = world()
    t = tower(c, 0, at(0))
    damage(c, t, 500)
    c.client_message(a, client.Died(), 5.0)
    assert use(c, a, "R") == []
    assert hp(c, t)[0] == policy.TOWER_HP - 500
    c2, (x,) = seat([0], reported=False)
    assert use(c2, x, "R") == []


# --- Repair, Harden, Immunize: own team only ---


def test_repair_restores_own_team_towers_to_full_and_leaves_the_enemys() -> None:
    c, (a, _b) = world()
    mine, theirs, mega = tower(c, 0, at(100)), tower(c, 1, at(100)), tower(c, 0, at(-100), True)
    for t in (mine, theirs, mega):
        damage(c, t, 900)
    assert use(c, a, "R") == []  # the client holds no tower health: nothing to send
    assert hp(c, mine) == (policy.TOWER_HP, policy.TOWER_HP)
    assert hp(c, mega) == (policy.MEGATOWER_HP, policy.MEGATOWER_HP)
    assert hp(c, theirs)[0] == policy.TOWER_HP - 900


def test_harden_doubles_current_and_maximum_once_per_tower() -> None:
    c, (a, _b) = world()
    mine, theirs, far = tower(c, 0, at(100)), tower(c, 1, at(100)), tower(c, 0, at(REACH + 1))
    damage(c, mine, 1000)
    use(c, a, "H")
    assert hp(c, mine) == (2 * (policy.TOWER_HP - 1000), 2 * policy.TOWER_HP)
    assert hp(c, theirs) == (policy.TOWER_HP, policy.TOWER_HP) and hp(c, far)[0] == policy.TOWER_HP
    use(c, a, "H", now=11.0)  # no stacking
    assert hp(c, mine) == (2 * (policy.TOWER_HP - 1000), 2 * policy.TOWER_HP)


def test_harden_then_repair_restores_to_the_doubled_maximum() -> None:
    c, (a, _b) = world()
    t = tower(c, 0, at(0))
    use(c, a, "H")
    damage(c, t, 4000)
    use(c, a, "R", now=11.0)
    assert hp(c, t) == (2 * policy.TOWER_HP, 2 * policy.TOWER_HP)


def test_a_hardened_tower_takes_twice_the_hits() -> None:
    c, (a, b) = world()
    t = tower(c, 0, at(0))
    use(c, a, "H")
    for i in range(20):  # the starting gun's 150 destroys a plain tower in 20 hits
        c.client_message(b, client.HitReport(t, 0), 20.0 + i)
    assert t in c.orders.towers


def test_immunize_marks_own_towers_in_reach_for_thirty_minutes() -> None:
    c, (a, _b) = world()
    mine, theirs, far = tower(c, 0, at(100)), tower(c, 1, at(100)), tower(c, 0, at(REACH + 1))
    use(c, a, "I", now=100.0)
    towers = c.orders.towers
    assert towers[mine].immune_until == 100.0 + 1800.0 == 100.0 + policy.IMMUNIZE_SECONDS
    assert towers[mine].immune(1899.9) and not towers[mine].immune(1900.0)
    assert not towers[theirs].immune(100.0) and not towers[far].immune(100.0)


# --- Liberator ---


def test_liberator_destroys_enemy_towers_in_reach_with_a_j_to_every_client() -> None:
    c, (a, b) = world()
    e1, e2 = tower(c, 1, at(200)), tower(c, 1, at(-REACH))
    out_of_reach, own = tower(c, 1, at(REACH + 1)), tower(c, 0, at(100))
    out = use(c, a, "L")
    for pid in (a, b):
        assert to(out, pid) == [server.Destroy(e1), server.Destroy(e2)]
    assert set(c.orders.towers) == {out_of_reach, own}


def test_liberated_towers_are_forgotten_and_a_late_joiner_is_not_given_them() -> None:
    c, (a, _b) = world()
    e = tower(c, 1, at(10))
    use(c, a, "L")
    assert e not in c.orders.towers
    late = c.join("LATE", 2, 20.0)
    assert not isinstance(late, str)
    pid = late.player_id  # type: ignore[union-attr]
    out = deliveries(c, c.client_message(pid, report(4), 21.0))
    assert not [m for m in to(out, pid) if isinstance(m, server.SpawnTower)]


def test_liberator_with_no_enemy_tower_in_reach_sends_nothing() -> None:
    c, (a, _b) = world()
    tower(c, 0, at(10))
    assert use(c, a, "L") == []
    assert len(c.orders.towers) == 1


def test_the_other_team_may_liberate_too() -> None:
    c, (a, b) = world()
    ours = tower(c, 0, at(10))
    out = use(c, b, "L")
    assert to(out, a) == [server.Destroy(ours)]


# --- Weather ---


def palettes(out: list[tuple[int, ServerMessage]], pid: int) -> list[int]:
    return [m.arg for m in to(out, pid) if isinstance(m, server.Ambient) and m.sub == 0]


def test_weather_cycles_0_to_4_and_back_with_an_r_sub_0_to_every_client() -> None:
    c, (a, b) = world()
    seen = []
    for i in range(6):
        out = use(c, a, "W", now=10.0 + i)
        assert palettes(out, a) == palettes(out, b) and len(palettes(out, a)) == 1
        seen.append(palettes(out, b)[0])
    assert seen == [1, 2, 3, 4, 0, 1]
    assert max(seen) <= 4  # the client crashes on a larger palette


def test_weather_is_not_sent_to_a_client_still_loading_but_it_gets_it_on_its_first_report() -> None:
    c, (a, b) = seat([0, 1], reported=False)
    pos = at(0)
    c.client_message(
        a, report(4, cell_x=pos.cell_x, cell_z=pos.cell_z, x_off=pos.x_off, z_off=pos.z_off), 1.0
    )
    out = use(c, a, "W")
    assert palettes(out, a) == [1] and to(out, b) == []
    later = deliveries(c, c.client_message(b, report(4), 12.0))
    assert palettes(later, b) == [1]


def test_an_emptied_city_starts_again_in_the_default_weather() -> None:
    c, (a, b) = world()
    use(c, a, "W")
    c.leave(a, 20.0)
    c.leave(b, 21.0)
    assert c.weather.palette == policy.DEFAULT_PALETTE


# --- Mine and Mine Sweep ---


def mines_in(out: list[tuple[int, ServerMessage]], pid: int) -> list[server.SpawnMine]:
    return [m for m in to(out, pid) if isinstance(m, server.SpawnMine)]


def lay(c: City, pid: int, now: float = 10.0) -> int:
    out = use(c, pid, "M", now)
    return mines_in(out, pid)[0].id


def test_a_mine_is_spawned_at_the_sender_for_everyone_with_the_senders_team_as_variant() -> None:
    c, (a, b, d) = world([2, 1, 0])
    out = use(c, a, "M")
    pos = c.player(a).pod.placed.pos  # type: ignore[union-attr]
    expected = server.SpawnMine(policy.STRUCTURE_IDS[0], 2, pos)
    for pid in (a, b, d):
        assert mines_in(out, pid) == [expected]
    assert c.effects.mines[expected.id].team == 2
    assert expected.id >= 8192


def test_a_mine_is_given_to_a_late_joiner_once() -> None:
    c, (a, _b) = world()
    mine_id = lay(c, a)
    late = c.join("LATE", 2, 20.0)
    pid = late.player_id  # type: ignore[union-attr]
    first = deliveries(c, c.client_message(pid, report(4), 21.0))
    assert [m.id for m in mines_in(first, pid)] == [mine_id]
    again = deliveries(c, c.client_message(pid, report(0), 22.0))
    assert mines_in(again, pid) == []


def test_mine_sweep_destroys_only_other_teams_mines_within_one_block() -> None:
    c, (a, b) = world()
    mine_a = lay(c, a)  # team 0's own, laid at HERE
    mine_b = lay(c, b)  # team 1's, at HERE too
    far = at(REACH + 1)
    c.client_message(
        b, report(0, cell_x=far.cell_x, cell_z=far.cell_z, x_off=far.x_off, z_off=far.z_off), 11.0
    )
    mine_far = lay(c, b, now=12.0)
    out = use(c, a, "D", now=13.0)
    for pid in (a, b):
        assert to(out, pid) == [server.Destroy(mine_b)]
    assert set(c.effects.mines) == {mine_a, mine_far}


def test_mine_sweep_reach_is_600_ft_inclusive() -> None:
    c, (a, b) = world()
    edge, past = at(REACH), at(REACH + 1)
    for where, now in ((edge, 11.0), (past, 12.0)):
        c.client_message(
            b,
            report(
                0, cell_x=where.cell_x, cell_z=where.cell_z, x_off=where.x_off, z_off=where.z_off
            ),
            now,
        )
        lay(c, b, now=now + 0.5)
    out = use(c, a, "D", now=14.0)
    assert len(to(out, a)) == 1 and len(c.effects.mines) == 1


def test_a_swept_mine_is_forgotten_for_late_joiners_and_its_id_is_released() -> None:
    c, (a, b) = world()
    mine_id = lay(c, b)
    use(c, a, "D")
    late = c.join("LATE", 2, 20.0)
    pid = late.player_id  # type: ignore[union-attr]
    out = deliveries(c, c.client_message(pid, report(4), 21.0))
    assert mines_in(out, pid) == [] and mine_id not in c.effects.mines


def test_mine_sweep_with_nothing_to_sweep_sends_nothing() -> None:
    c, (a, _b) = world()
    lay(c, a)
    assert use(c, a, "D", now=11.0) == []


def test_the_city_keeps_at_most_max_mines_and_destroys_the_oldest() -> None:
    c, (a, b) = world()
    first = lay(c, a, now=10.0)
    for i in range(policy.MAX_MINES - 1):
        lay(c, a, now=10.1 + i)
    assert len(c.effects.mines) == policy.MAX_MINES
    out = use(c, a, "M", now=500.0)
    assert to(out, b)[0] == server.Destroy(first) and len(c.effects.mines) == policy.MAX_MINES


def test_a_hit_on_a_mine_is_ignored() -> None:
    c, (a, b) = world()
    mine_id = lay(c, a)
    assert deliveries(c, c.client_message(b, client.HitReport(mine_id, 0), 12.0)) == []
    assert mine_id in c.effects.mines


# --- not this milestone's ---


def test_call_muncher_call_sentinel_and_the_drone_are_logged_and_ignored() -> None:
    c, (a, _b) = world()
    t = tower(c, 1, at(10))
    for letter in "CS":
        assert use(c, a, letter) == []
    assert c.client_message(a, client.Drone(None), 10.0) == []
    assert t in c.orders.towers and not c.effects.mines


def test_an_emptied_city_forgets_its_mines_and_drops() -> None:
    c, (a, b) = world()
    lay(c, a)
    c.drops.create(6, WirePos(0, 0, 1, 1, 1), 5.0)
    c.leave(a, 20.0)
    c.leave(b, 21.0)
    assert not c.effects.mines and not c.drops.drops


# --- only what the city stocks (our pickup, eject and effect rules; see I §9.3.3, II §16.1) ---


def test_an_effect_above_the_cities_level_is_ignored() -> None:
    c, (a, _b) = world(max_module_level=3)  # Liberator is level 4
    e = tower(c, 1, at(10))
    assert use(c, a, "L") == []
    assert e in c.orders.towers


def test_a_banned_effect_is_ignored_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    c, (a, _b) = world(banned_modules=(6,))  # Repair
    t = tower(c, 0, at(10))
    damage(c, t, 100)
    before = hp(c, t)
    with caplog.at_level(logging.INFO, logger="opencs.world"):
        assert use(c, a, "R") == []
    assert hp(c, t) == before
    assert "does not stock" in caplog.text
    use(c, a, "H")  # an effect that is not banned still works
    assert hp(c, t)[1] == 2 * before[1]


def test_immunize_again_restarts_the_thirty_minutes_and_does_not_add_to_them() -> None:
    c, (a, _b) = world()
    t = tower(c, 0, at(100))
    use(c, a, "I", now=100.0)
    use(c, a, "I", now=700.0)
    tw = c.orders.towers[t]
    assert tw.immune_until == 700.0 + policy.IMMUNIZE_SECONDS
    assert tw.immune(700.0 + policy.IMMUNIZE_SECONDS - 0.1)
    assert not tw.immune(700.0 + policy.IMMUNIZE_SECONDS)
