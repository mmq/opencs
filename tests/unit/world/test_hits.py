"""The city's handling of an ``'I'``: damage relay and credit, kept apart (I §9.1, II §9.3,
I §9.1.4, II §9)."""

from __future__ import annotations

import pytest

from fixtures.cities import Npc, seat
from fixtures.delivery import deliveries, to
from fixtures.pods import report
from opencs.messages import client, server
from opencs.world.city import City


def fire(c: City, pid: int, now: float, shooter: int | None = None, kind: int = 1) -> None:
    c.client_message(
        pid, client.Shot(pid if shooter is None else shooter, report(0).pos, 0, 0, kind), now
    )


def playing(teams: list[int]) -> tuple[City, list[int]]:
    """Everyone has left drop state, so they can be hit."""
    c, ids = seat(teams)
    for pid in ids:
        c.client_message(pid, report(0), 1.0)
    return c, ids


def hit(target: int, damage: int = 10) -> client.HitReport:
    return client.HitReport(target, damage)


def test_damage_is_relayed_to_the_victim_alone_with_the_byte_verbatim() -> None:
    c, (a, b, d) = playing([0, 1, 2])
    out = deliveries(c, c.client_message(a, hit(b, 23), 5.0))
    assert out == [(b, server.Damage(a, 23))]
    assert d not in [pid for pid, _ in out]


def test_damage_is_relayed_whatever_the_team() -> None:
    c, (a, b) = playing([1, 1])  # friendly fire: II §9.1
    assert deliveries(c, c.client_message(a, hit(b, 20), 5.0)) == [(b, server.Damage(a, 20))]


def test_damage_is_relayed_even_when_no_shot_credits_the_hit() -> None:
    # A Sentinel's shot hurts the player it hits though it credits nobody (II §9.1).
    c, (a, b) = playing([0, 1])
    c.spawn(Npc(4100))
    fire(c, a, 4.0, shooter=4100, kind=2)
    out = deliveries(c, c.client_message(a, hit(b, 20), 5.0))
    assert out == [(b, server.Damage(a, 20))]
    victim = c.player(b)
    assert victim is not None and victim.hits.payees(5.0) == []


def test_a_report_naming_the_reporters_own_pod_is_never_relayed() -> None:
    c, (a, b) = playing([0, 1])
    assert c.client_message(a, hit(a), 5.0) == []
    assert c.own_pod_hit_reports == 1
    assert c.client_message(a, hit(a, 99), 5.0) == []
    me = c.player(a)
    assert me is not None and me.hits.payees(5.0) == []  # no self credit either
    assert b not in [pid for pid, _ in deliveries(c, c.client_message(a, hit(a), 5.0))]


@pytest.mark.parametrize("damage", [4, 24, 234])
def test_classes_up_to_4_are_relayed(damage: int) -> None:
    c, (a, b) = playing([0, 1])
    assert deliveries(c, c.client_message(a, hit(b, damage), 5.0)) == [
        (b, server.Damage(a, damage))
    ]
    assert c.unrelayable_damage == 0


@pytest.mark.parametrize("damage", [5, 9, 15, 239])
def test_classes_past_the_clients_table_are_dropped_and_counted(damage: int) -> None:
    c, (a, b) = playing([0, 1])
    assert c.client_message(a, hit(b, damage), 5.0) == []
    assert c.unrelayable_damage == 1


def test_an_own_pod_shot_a_second_earlier_credits_the_reporter() -> None:
    c, (a, b) = playing([0, 1])
    fire(c, a, 4.0)
    c.client_message(a, hit(b), 5.0)
    victim = c.player(b)
    assert victim is not None and victim.hits.payees(5.0) == [a]


def test_a_second_report_needs_a_second_shot() -> None:
    c, (a, b) = playing([0, 1])
    fire(c, a, 4.0)
    c.client_message(a, hit(b), 5.0)
    victim = c.player(b)
    assert victim is not None
    c.client_message(a, hit(b), 5.2)  # the shot was consumed: relayed, but no new credit
    assert victim.hits.payees(25.1) == []  # the first credit's 20 s ran out; no refresh at 5.2
    assert victim.hits.payees(25.0) == [a]


def test_a_hit_with_no_shot_in_the_log_is_not_credited_but_still_damages() -> None:
    c, (a, b) = playing([0, 1])
    out = deliveries(c, c.client_message(a, hit(b), 5.0))
    assert len(out) == 1
    victim = c.player(b)
    assert victim is not None and victim.hits.payees(5.0) == []


def test_the_shot_log_is_per_connection_so_a_rivals_shot_credits_nobody_else() -> None:
    c, (a, b, d) = playing([0, 1, 2])
    fire(c, b, 4.0)  # b fired
    c.client_message(a, hit(d), 5.0)  # a reports: nothing in a's own log
    victim = c.player(d)
    assert victim is not None and victim.hits.payees(5.0) == []


def test_a_hit_on_an_entity_uses_up_a_shot_and_credits_and_relays_nothing() -> None:
    # II §9.3 ground truth: p2's 9 reports on a mine, caused by the Sentinel, credit nobody.
    c, (a, b) = playing([0, 1])
    c.spawn(Npc(38))
    fire(c, a, 4.0)
    assert c.client_message(a, hit(38), 5.0) == []
    c.client_message(a, hit(b), 5.1)  # the own shot is gone, so this hit has nothing to credit
    victim = c.player(b)
    assert victim is not None and victim.hits.payees(5.1) == []


def test_damage_to_a_dead_pod_is_dropped() -> None:
    c, (a, b) = playing([0, 1])
    c.client_message(b, client.Died(), 5.0)
    assert c.client_message(a, hit(b), 5.5) == []


def test_damage_to_a_pod_that_has_not_reported_is_dropped() -> None:
    c, ids = seat([0, 1], reported=False)
    c.client_message(ids[0], report(4), 1.0)
    assert c.client_message(ids[0], hit(ids[1]), 2.0) == []


def test_the_victim_receives_it_after_the_reporter_is_introduced() -> None:
    c, (a, b) = playing([0, 1])
    out = deliveries(c, c.client_message(a, hit(b, 20), 5.0))
    assert [type(m) for m in to(out, b)] == [server.Damage]
