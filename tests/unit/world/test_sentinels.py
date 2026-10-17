"""Sentinels: placement, the late-joiner sync, durability, replacement and the called one
(I §7.3, I §9.1.3, I §9.5.2, II §16.3)."""

from __future__ import annotations

import random

import pytest

from fixtures.delivery import deliveries, to
from fixtures.lobbies import city as config_for
from fixtures.pods import report
from opencs import policy
from opencs.messages import ServerMessage, WirePos, client, server
from opencs.world.city import City
from opencs.world.npcs.sentinels import eligible_blocks

RESIDENT_LOW = policy.SENTINEL_IDS[0]
CALLED_LOW = policy.STRUCTURE_IDS[0]
ECHO = 4  # a damage byte of weapon class 4: 500 off a Sentinel


def city(rng_seed: int = 1, **config: object) -> tuple[City, list[int]]:
    config = {"sentinels": 4, "sentinel_respawn": 300.0, "max_module_level": 5, **config}
    c = City(config_for("B", **config), random.Random(rng_seed))
    ids = []
    for team in (0, 1):
        r = c.join(f"P{team}", team, 0.0)
        assert hasattr(r, "player_id")
        ids.append(r.player_id)  # type: ignore[union-attr]
    return c, ids


def report_all(c: City, ids: list[int], now: float = 1.0) -> list[tuple[int, ServerMessage]]:
    out = []
    for pid in ids:
        out += deliveries(c, c.client_message(pid, report(4), now))
    return out


def spawns(out: list[tuple[int, ServerMessage]], pid: int) -> list[server.SpawnSentinel]:
    return [m for m in to(out, pid) if isinstance(m, server.SpawnSentinel)]


def destroys(out: list[tuple[int, ServerMessage]], pid: int) -> list[int]:
    return [m.id for m in to(out, pid) if isinstance(m, server.Destroy)]


def hit(c: City, pid: int, target: int, damage: int = ECHO, now: float = 5.0) -> list:  # type: ignore[type-arg]
    return deliveries(c, c.client_message(pid, client.HitReport(target, damage), now))


# --- placement ----------------------------------------------------------------------------------


def test_the_eligible_blocks_are_the_48_outside_the_corners_3x3_areas() -> None:
    blocks = eligible_blocks()
    assert len(blocks) == 48 and len(set(blocks)) == 48
    for cx, cz in policy.TEAM_CORNER_CELLS:
        assert not [b for b in blocks if abs(b[0] - cx) <= 1 and abs(b[1] - cz) <= 1]
    # The edge of the excluded area is eligible: 2 tiles from the corner on either axis.
    assert (2, 0) in blocks and (0, 2) in blocks and (7, 2) in blocks and (5, 7) in blocks
    assert (1, 1) not in blocks and (6, 6) not in blocks and (1, 0) not in blocks


@pytest.mark.parametrize("seed", range(25))
def test_residents_stand_one_per_block_150_to_450_feet_in(seed: int) -> None:
    c, _ = city(seed, sentinels=12)
    standing = list(c.sentinels.alive.values())
    assert [s.entity_id for s in standing] == list(range(RESIDENT_LOW, RESIDENT_LOW + 12))
    blocks = [(s.pos.world_x // 600, s.pos.world_z // 600) for s in standing]
    assert len(set(blocks)) == 12 and set(blocks) <= set(eligible_blocks())
    for s in standing:
        low, high = policy.SENTINEL_OFFSET_FEET
        assert low <= s.pos.world_x % 600 <= high and low <= s.pos.world_z % 600 <= high


def test_the_same_seed_places_them_the_same_way_and_another_seed_differently() -> None:
    def spots(seed: int) -> list[tuple[int, int]]:
        c, _ = city(seed)
        return [(s.pos.world_x, s.pos.world_z) for s in c.sentinels.alive.values()]

    assert spots(3) == spots(3) and spots(3) != spots(4)


def test_a_city_with_none_configured_has_none() -> None:
    c, _ = city(sentinels=0)
    assert not c.sentinels.alive


def test_the_most_a_city_may_ask_for_fills_every_eligible_block() -> None:
    c, _ = city(sentinels=policy.MAX_SENTINELS)
    taken = {(s.pos.world_x // 600, s.pos.world_z // 600) for s in c.sentinels.alive.values()}
    assert taken == set(eligible_blocks())


# --- the sync -----------------------------------------------------------------------------------


def test_every_client_is_sent_each_sentinel_once_on_its_first_report() -> None:
    c, (a, b) = city()
    out = report_all(c, [a, b])
    for pid in (a, b):
        got = spawns(out, pid)
        assert [m.id for m in got] == list(range(RESIDENT_LOW, RESIDENT_LOW + 4))
        assert got == [server.SpawnSentinel(s.entity_id, s.pos) for s in c.sentinels.alive.values()]
    again = deliveries(c, c.client_message(a, report(0), 2.0))
    assert spawns(again, a) == []


def test_a_late_joiner_is_given_the_live_sentinels_but_not_a_destroyed_one() -> None:
    c, (a, b) = city()
    report_all(c, [a, b])
    for _ in range(6):  # 3000 / 500
        hit(c, a, RESIDENT_LOW)
    r = c.join("LATE", 2, 20.0)
    assert hasattr(r, "player_id")
    out = deliveries(c, c.client_message(r.player_id, report(4), 21.0))  # type: ignore[union-attr]
    ids = [m.id for m in spawns(out, r.player_id)]  # type: ignore[union-attr]
    assert ids == [RESIDENT_LOW + 1, RESIDENT_LOW + 2, RESIDENT_LOW + 3]


def test_a_client_still_loading_is_sent_the_sentinels_by_its_first_report_not_before() -> None:
    c, (a, b) = city()
    report_all(c, [a])
    assert spawns(deliveries(c, c.tick(2.0)), b) == []
    out = deliveries(c, c.client_message(b, report(4), 3.0))
    assert len(spawns(out, b)) == 4


# --- durability ---------------------------------------------------------------------------------


def test_a_sentinel_takes_3000_on_the_per_shot_table_and_falls_at_zero() -> None:
    c, (a, b) = city()
    report_all(c, [a, b])
    for n in range(5):  # 5 x 500 = 2500: standing
        assert hit(c, a, RESIDENT_LOW) == [], n
    out = hit(c, a, RESIDENT_LOW)  # the sixth takes it to exactly 0
    assert destroys(out, a) == [RESIDENT_LOW] and destroys(out, b) == [RESIDENT_LOW]
    assert RESIDENT_LOW not in c.sentinels.alive


def test_each_weapon_class_takes_its_table_entry_whoever_fires() -> None:
    c, (a, b) = city(sentinels=1)
    report_all(c, [a, b])
    s = c.sentinels.alive[RESIDENT_LOW]
    hit(c, a, RESIDENT_LOW, 0)
    hit(c, b, RESIDENT_LOW, 12)  # magnitude digit ignored; class 2: 300
    hit(c, a, RESIDENT_LOW, 3)
    assert s.hp == policy.SENTINEL_HP - 150 - 300 - 450


def test_a_weapon_class_past_the_table_does_nothing() -> None:
    c, (a, b) = city(sentinels=1)
    report_all(c, [a, b])
    hit(c, a, RESIDENT_LOW, 5)
    hit(c, a, RESIDENT_LOW, 9)
    assert c.sentinels.alive[RESIDENT_LOW].hp == policy.SENTINEL_HP


def test_the_destroy_goes_only_to_clients_that_hold_the_sentinel() -> None:
    c, (a, b) = city()
    report_all(c, [a])  # b is still loading: it has never been sent the Sentinel
    for _ in range(6):
        out = hit(c, a, RESIDENT_LOW)
    assert destroys(out, a) == [RESIDENT_LOW] and destroys(out, b) == []


def test_a_hit_on_an_unknown_id_is_ignored() -> None:
    c, (a, b) = city()
    report_all(c, [a, b])
    assert hit(c, a, RESIDENT_LOW + 100) == []
    assert all(s.hp == policy.SENTINEL_HP for s in c.sentinels.alive.values())


# --- replacement --------------------------------------------------------------------------------


def test_a_destroyed_resident_is_replaced_after_the_respawn_time_with_a_new_id() -> None:
    c, (a, b) = city(sentinels=2)
    report_all(c, [a, b])
    for _ in range(6):
        hit(c, a, RESIDENT_LOW, now=100.0)
    assert spawns(deliveries(c, c.tick(399.9)), a) == []
    out = deliveries(c, c.tick(400.0))
    new = spawns(out, a)
    assert len(new) == 1 and spawns(out, b) == new
    assert new[0].id not in (RESIDENT_LOW, RESIDENT_LOW + 1)
    assert new[0].id == RESIDENT_LOW + 2
    survivor = c.sentinels.alive[RESIDENT_LOW + 1]
    placed = c.sentinels.alive[new[0].id]
    assert (placed.pos.world_x // 600, placed.pos.world_z // 600) != (
        survivor.pos.world_x // 600,
        survivor.pos.world_z // 600,
    )
    assert spawns(deliveries(c, c.tick(401.0)), a) == []
    assert len([s for s in c.sentinels.alive.values() if s.resident]) == 2


def test_a_replacement_is_not_made_twice() -> None:
    c, (a, b) = city(sentinels=1)
    report_all(c, [a, b])
    for _ in range(6):
        hit(c, a, RESIDENT_LOW, now=100.0)
    c.tick(400.0)
    c.tick(500.0)
    c.tick(900.0)
    assert len(c.sentinels.alive) == 1


def test_an_emptied_city_forgets_its_sentinels_and_starts_again() -> None:
    c, (a, b) = city()
    report_all(c, [a, b])
    c.leave(a, 10.0)
    c.leave(b, 11.0)
    assert not c.sentinels.alive
    r = c.join("NEW", 0, 50.0)
    assert hasattr(r, "player_id")
    assert [s.entity_id for s in c.sentinels.alive.values()] == list(
        range(RESIDENT_LOW, RESIDENT_LOW + 4)
    )


# --- called Sentinels ---------------------------------------------------------------------------


def call(c: City, pid: int, now: float = 10.0) -> list:  # type: ignore[type-arg]
    return deliveries(c, c.client_message(pid, client.EffectRequest("S"), now))


def stand(c: City, pid: int, x: int, z: int) -> WirePos:
    pos = WirePos.from_world(x, 20, z)
    c.client_message(
        pid, report(0, cell_x=pos.cell_x, cell_z=pos.cell_z, x_off=pos.x_off, z_off=pos.z_off), 2.0
    )
    return pos


def test_call_sentinel_makes_one_at_the_senders_position_with_a_structure_id() -> None:
    c, (a, b) = city(sentinels=0)
    report_all(c, [a, b])
    pos = stand(c, a, 1777, 2555)
    out = call(c, a)
    for pid in (a, b):
        (made,) = spawns(out, pid)
        assert made.id == CALLED_LOW
        assert (made.pos.world_x, made.pos.world_z) == (pos.world_x, pos.world_z)
    assert not c.sentinels.alive[CALLED_LOW].resident


def test_a_called_sentinel_has_the_same_durability_and_is_not_replaced() -> None:
    c, (a, b) = city(sentinels=0)
    report_all(c, [a, b])
    call(c, a)
    for _ in range(5):
        assert hit(c, b, CALLED_LOW) == []
    out = hit(c, b, CALLED_LOW)
    assert destroys(out, a) == [CALLED_LOW]
    c.tick(10_000.0)
    assert not c.sentinels.alive


def test_called_and_resident_sentinels_do_not_share_an_id_range() -> None:
    c, (a, b) = city(sentinels=1)
    report_all(c, [a, b])
    call(c, a)
    assert sorted(c.sentinels.alive) == [RESIDENT_LOW, CALLED_LOW]


def test_a_late_joiner_is_given_a_called_sentinel() -> None:
    c, (a, b) = city(sentinels=0)
    report_all(c, [a, b])
    call(c, a)
    r = c.join("LATE", 2, 20.0)
    assert hasattr(r, "player_id")
    out = deliveries(c, c.client_message(r.player_id, report(4), 21.0))  # type: ignore[union-attr]
    assert [m.id for m in spawns(out, r.player_id)] == [CALLED_LOW]  # type: ignore[union-attr]


def test_the_oldest_called_sentinel_goes_at_the_cap() -> None:
    c, (a, b) = city(sentinels=0)
    report_all(c, [a, b])
    for _ in range(policy.MAX_CALLED_SENTINELS):
        call(c, a)
    assert len(c.sentinels.alive) == policy.MAX_CALLED_SENTINELS
    out = call(c, a)
    assert destroys(out, b) == [CALLED_LOW]
    assert len(c.sentinels.alive) == policy.MAX_CALLED_SENTINELS


def test_call_sentinel_needs_a_pod_in_play_and_a_stocked_module() -> None:
    c, (a, b) = city(sentinels=0, max_module_level=0)
    report_all(c, [a, b])
    assert call(c, a) == [] and not c.sentinels.alive


# --- Sentinels fire on their own ----------------------------------------------------------------


def test_a_sentinels_shot_is_relayed_logged_and_credits_nobody() -> None:
    c, (a, b) = city()
    report_all(c, [a, b])
    shot = client.Shot(RESIDENT_LOW, report(0).pos, 3196, 0, 2)
    out = deliveries(c, c.client_message(a, shot, 10.0))
    assert [m for _, m in out] == [server.Shot(RESIDENT_LOW, shot.pos, 3196, 0, 2)]
    assert [p for p, _ in out] == [b]
    assert c.shooter_mismatches == 0
    pa = c.player(a)
    assert pa is not None and len(pa.shots) == 1
    # A hit on b reported a second later is the Sentinel's, not a's: no credit.
    out = deliveries(c, c.client_message(a, client.HitReport(b, 2), 11.0))
    pb = c.player(b)
    assert pb is not None and pb.hits.payees(11.0) == []
    assert [type(m) for _, m in out] == [server.Damage]  # the damage still lands (S10.1)


def test_a_shot_by_an_id_that_is_not_a_known_npc_is_a_mismatch() -> None:
    c, (a, b) = city(sentinels=1)
    report_all(c, [a, b])
    shot = client.Shot(RESIDENT_LOW + 1, report(0).pos, 3196, 0, 2)
    c.client_message(a, shot, 10.0)
    assert c.shooter_mismatches == 1
