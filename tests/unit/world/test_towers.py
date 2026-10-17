"""Towers: placed for everyone, hurt by any hit report, destroyed at 0 (I §9.4.1, II §10)."""

from __future__ import annotations

import pytest

from fixtures.cities import seat
from fixtures.delivery import deliveries, to
from fixtures.pods import report
from opencs import policy
from opencs.messages import WirePos, client, server
from opencs.world.city import City
from opencs.world.ids import IdRange

POS = WirePos(3, 2, 250, 20, 410)


def playing(teams: list[int]) -> tuple[City, list[int]]:
    c, ids = seat(teams)
    for pid in ids:
        c.client_message(pid, report(0), 1.0)
    return c, ids


def hit(target: int, damage: int) -> client.HitReport:
    return client.HitReport(target, damage)


def place(c: City, team: int = 0, mega: bool = False, now: float = 2.0) -> int:
    out = deliveries(c, c.place_tower(team, POS, mega, now))
    (spawn,) = {m for _, m in out}
    assert isinstance(spawn, server.SpawnTower)
    return spawn.id


def test_a_tower_is_spawned_for_every_client_that_has_reported() -> None:
    c, (a, b) = playing([0, 1])
    out = deliveries(c, c.place_tower(0, POS, False, 2.0))
    tower_id = policy.STRUCTURE_IDS[0]
    expected = server.SpawnTower(0, tower_id, 0, POS, 0)
    assert out == [(a, expected), (b, expected)]


def test_a_megatower_is_flagged_and_the_team_is_the_orderers() -> None:
    c, (a, _b) = playing([0, 2])
    out = deliveries(c, c.place_tower(2, POS, True, 2.0))
    assert to(out, a)[0] == server.SpawnTower(0, policy.STRUCTURE_IDS[0], 2, POS, 1)


def test_a_client_still_loading_is_not_sent_the_tower_but_gets_it_when_it_reports() -> None:
    c, (a, b) = seat([0, 1], reported=False)
    c.client_message(a, report(4), 1.0)
    out = deliveries(c, c.place_tower(0, POS, False, 2.0))
    assert to(out, b) == [] and len(to(out, a)) == 1
    later = deliveries(c, c.client_message(b, report(4), 3.0))
    towers = [m for m in to(later, b) if isinstance(m, server.SpawnTower)]
    assert towers == [server.SpawnTower(0, policy.STRUCTURE_IDS[0], 0, POS, 0)]
    # And never twice.
    again = deliveries(c, c.client_message(b, report(0), 4.0))
    assert [m for m in to(again, b) if isinstance(m, server.SpawnTower)] == []


@pytest.mark.parametrize(
    ("byte", "hits"),
    [(10, 20), (11, 20), (12, 10), (13, 7), (14, 6)],  # 3000 / 150, 150, 300, 450, 500
)
def test_each_weapon_class_takes_its_table_entry_off(byte: int, hits: int) -> None:
    c, (a, b) = playing([0, 1])
    tower = place(c)
    for n in range(hits - 1):
        assert c.client_message(a, hit(tower, byte), 5.0 + n) == [], f"destroyed after {n + 1}"
    out = deliveries(c, c.client_message(a, hit(tower, byte), 30.0))
    assert out == [(a, server.Destroy(tower)), (b, server.Destroy(tower))]


def test_twenty_starting_gun_hits_destroy_a_tower_and_the_nineteenth_does_not() -> None:
    c, (a, _) = playing([0, 1])
    tower = place(c)
    outs = [c.client_message(a, hit(tower, 0), 5.0 + n) for n in range(19)]
    assert all(o == [] for o in outs)
    assert deliveries(c, c.client_message(a, hit(tower, 0), 30.0))


def test_a_megatower_takes_twice_as_many() -> None:
    c, (a, _) = playing([0, 1])
    tower = place(c, mega=True)
    for n in range(39):
        assert c.client_message(a, hit(tower, 0), 5.0 + n) == []
    assert deliveries(c, c.client_message(a, hit(tower, 0), 50.0))


def test_the_magnitude_digit_is_ignored() -> None:
    c, (a, _) = playing([0, 1])
    tower = place(c)
    for n in range(19):
        c.client_message(a, hit(tower, 80), 5.0 + n)  # class 0 with a big tens digit
    assert deliveries(c, c.client_message(a, hit(tower, 0), 30.0))


def test_friendly_fire_counts() -> None:
    c, (_a, b) = playing([0, 0])
    tower = place(c, team=0)
    for n in range(19):
        c.client_message(b, hit(tower, 0), 5.0 + n)
    assert deliveries(c, c.client_message(b, hit(tower, 0), 30.0))


def test_a_damage_class_past_the_clients_table_is_ignored() -> None:
    c, (a, _) = playing([0, 1])
    tower = place(c)
    for n in range(40):
        assert c.client_message(a, hit(tower, 15), 5.0 + n) == []
        assert c.client_message(a, hit(tower, 19), 5.0 + n) == []
    # Still standing: it takes all 20 hits afterward.
    for n in range(19):
        assert c.client_message(a, hit(tower, 0), 50.0 + n) == []
    assert deliveries(c, c.client_message(a, hit(tower, 0), 80.0))


def test_a_destroyed_tower_is_forgotten_a_late_joiner_is_not_given_it_and_its_id_is_released() -> (
    None
):
    c, (a, b) = playing([0, 1])
    tower = place(c)
    for n in range(20):
        c.client_message(a, hit(tower, 0), 5.0 + n)
    assert not c.player(a).view.holds(tower)  # type: ignore[union-attr]
    assert not c.player(b).view.holds(tower)  # type: ignore[union-attr]
    # A hit on the dead tower is ignored.
    assert c.client_message(a, hit(tower, 0), 40.0) == []
    # A client that joins now is given no tower.
    joiner = c.join("LATE", 2, 41.0)
    assert not isinstance(joiner, str) and hasattr(joiner, "player_id")
    out = deliveries(c, c.client_message(joiner.player_id, report(4), 42.0))  # type: ignore[union-attr]
    assert [m for _, m in out if isinstance(m, server.SpawnTower)] == []
    # The id went back to its range (reused only after the range runs out, I §6.2, II §8).
    assert c._ids._allocators[IdRange.STRUCTURE]._released[0][0] == tower


def test_destruction_names_only_the_clients_that_hold_the_tower() -> None:
    c, (a, b) = seat([0, 1], reported=False)
    c.client_message(a, report(4), 1.0)
    c.client_message(a, report(0), 1.5)
    tower = place(c, now=2.0)
    assert not c.player(b).view.holds(tower)  # type: ignore[union-attr]  # not reported yet
    for n in range(19):
        c.client_message(a, hit(tower, 0), 5.0 + n)
    out = deliveries(c, c.client_message(a, hit(tower, 0), 30.0))
    assert out == [(a, server.Destroy(tower))]


def test_a_hit_on_a_tower_still_uses_up_the_reporters_shot() -> None:
    c, (a, _) = playing([0, 1])
    tower = place(c)
    c.client_message(a, client.Shot(a, report(0).pos, 0, 0, 1), 4.0)
    player = c.player(a)
    assert player is not None and len(player.shots) == 1
    c.client_message(a, hit(tower, 0), 5.0)
    assert len(player.shots) == 0


def test_territory_income_follows_the_towers_the_server_knows() -> None:
    # POS is in block (3, 2), made worth 10 (class 3) in this city's map.
    blocks = tuple(tuple(3 if (x, z) == (3, 2) else 0 for x in range(8)) for z in range(8))
    c, ids = seat([0, 1], blocks=blocks)
    for pid in ids:
        c.client_message(pid, report(0), 1.0)
    assert c.territory_income() == {}
    c.place_tower(0, POS, False, 2.0)
    assert c.territory_income() == {0: 10}
    rival = place(c, team=1, now=3.0)  # a rival in the same block: disputed
    assert c.territory_income() == {}
    for n in range(20):
        c.client_message(ids[0], hit(rival, 0), 5.0 + n)  # the rival falls: team 0 holds it again
    assert c.territory_income() == {0: 10}
