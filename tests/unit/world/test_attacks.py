"""What puts a pod "under attack" for /RESUME's fight test (II §13 item 3, D4): hit reports and
damage, and enemy shots and grenades within 600 ft. Teammates, the pod's own fire and distance are
the cases that must not count."""

from __future__ import annotations

from dataclasses import replace

import pytest

from fixtures.cities import Npc, seat
from fixtures.pods import report
from opencs import policy
from opencs.messages import WirePos, client
from opencs.world.attacks import Attacks, in_fight_range
from opencs.world.city import City


def at(x: int, z: int = 3000) -> client.PodState:
    """A state-0 report standing at world feet (x, z)."""
    return replace(report(0), pos=WirePos.from_world(x, 20, z))


def field_of(teams: list[int], xs: list[int]) -> tuple[City, list[int]]:
    """Players of these teams, each standing at world x ``xs[i]`` (z fixed), all in play."""
    c, ids = seat(teams)
    for pid, x in zip(ids, xs, strict=True):
        c.client_message(pid, at(x), 1.0)
    return c, ids


def last(c: City, pid: int) -> str:
    p = c.player(pid)
    assert p is not None
    return p.attacks.since(0.0)


def shoot(c: City, sender: int, shooter: int, x: int, now: float = 5.0, kind: int = 1) -> None:
    c.client_message(sender, client.Shot(shooter, WirePos.from_world(x, 20, 3000), 0, 0, kind), now)


def test_attacks_remember_the_last_time_and_why() -> None:
    a = Attacks()
    assert a.since(0.0) == ""
    a.mark(5.0, "X")
    assert (a.since(5.0), a.since(5.1)) == ("X", "")  # at or after the cut
    a.mark(9.0, "Y")
    assert a.since(6.0) == "Y"


@pytest.mark.parametrize(("feet", "near"), [(0, True), (600, True), (601, False)])
def test_fight_range_is_600_feet_inclusive(feet: int, near: bool) -> None:
    assert policy.FIGHT_RANGE == 600
    a, b = WirePos.from_world(1000, 20, 3000), WirePos.from_world(1000 + feet, 20, 3000)
    assert in_fight_range(a, b) is near


def test_range_ignores_altitude() -> None:
    a, b = WirePos.from_world(1000, 0, 3000), WirePos.from_world(1000, 900, 3000)
    assert in_fight_range(a, b)


def test_an_enemy_shot_within_range_is_an_attack_on_the_pod() -> None:
    c, (a, b) = field_of([0, 1], [1000, 3000])
    shoot(c, b, b, 1600)  # b fires 600 ft from a
    assert last(c, a) != ""
    assert last(c, b) == ""  # its own fire is not an attack on itself


def test_an_enemy_shot_beyond_range_is_not() -> None:
    c, (a, b) = field_of([0, 1], [1000, 3000])
    shoot(c, b, b, 1601)
    assert last(c, a) == ""


def test_a_teammates_shot_is_not_an_attack() -> None:
    c, (a, b) = field_of([2, 2], [1000, 1100])
    shoot(c, b, b, 1100)
    assert last(c, a) == ""


def test_an_npc_shot_is_an_attack_on_every_pod_in_range_even_the_reporters() -> None:
    c, (a, b, d) = field_of([0, 0, 1], [1000, 1200, 5000])
    c.spawn(Npc(4100), 0.0)
    shoot(c, a, 4100, 1300)  # a's client simulates the NPC
    assert last(c, a) != "" and last(c, b) != ""
    assert last(c, d) == ""


def test_an_unknown_shooter_is_treated_as_an_npc() -> None:
    c, (a, b) = field_of([0, 0], [1000, 1100])
    shoot(c, b, 9999, 1000)
    assert last(c, a) != "" and last(c, b) != ""


def test_a_shot_the_session_dropped_still_counts() -> None:
    c, (a, b) = field_of([0, 1], [1000, 1100])
    c.note_shot(b, client.Shot(b, WirePos.from_world(1100, 20, 3000), 0, 0, 1), 5.0)
    assert last(c, a) != ""


def test_a_shot_aimed_from_an_unsafe_kind_is_still_a_fight() -> None:
    c, (a, b) = field_of([0, 1], [1000, 1100])
    shoot(c, b, b, 1100, kind=99)  # not relayed (it would crash a client), but fired
    assert c.unsafe_shots == 1 and last(c, a) != ""


def test_a_pod_that_died_is_not_marked() -> None:
    c, (a, b) = field_of([0, 1], [1000, 1100])
    c.client_message(a, client.Died(), 3.0)
    shoot(c, b, b, 1100, now=5.0)
    assert last(c, a) == ""


def test_an_enemy_grenade_within_range_is_an_attack() -> None:
    c, (a, b) = field_of([0, 1], [1000, 3000])
    near = client.Grenade(WirePos.from_world(1600, 20, 3000), 0, 0)
    c.client_message(b, near, 5.0)
    assert last(c, a) != ""


def test_an_enemy_grenade_out_of_range_and_a_teammates_are_not() -> None:
    c, (a, b, d) = field_of([0, 1, 0], [1000, 3000, 1100])
    c.client_message(b, client.Grenade(WirePos.from_world(1601, 20, 3000), 0, 0), 5.0)
    c.client_message(d, client.Grenade(WirePos.from_world(1100, 20, 3000), 0, 0), 5.0)
    assert last(c, a) == ""


def test_a_non_teammates_hit_report_naming_the_pod_is_an_attack() -> None:
    c, (a, b) = field_of([0, 1], [1000, 5000])
    c.client_message(b, client.HitReport(a, 99), 5.0)  # a damage class the client cannot index
    assert last(c, a) == "A HIT REPORT"  # reported, though no 'I' could be delivered


def test_damage_delivered_is_an_attack_even_from_a_teammate() -> None:
    c, (a, b) = field_of([0, 0], [1000, 5000])
    c.client_message(b, client.HitReport(a, 10), 5.0)
    assert last(c, a) == "DAMAGE"


def test_a_hit_on_something_else_marks_nobody() -> None:
    c, (a, b) = field_of([0, 1], [1000, 1100])
    c.spawn(Npc(38), 0.0)
    c.client_message(b, client.HitReport(38, 10), 5.0)
    assert last(c, a) == "" and last(c, b) == ""


def test_a_teammates_hit_report_that_delivers_nothing_is_not_an_attack() -> None:
    c, (a, b) = field_of([0, 0], [1000, 5000])
    c.client_message(b, client.HitReport(a, 99), 5.0)  # no 'I' can be delivered either
    assert last(c, a) == ""
