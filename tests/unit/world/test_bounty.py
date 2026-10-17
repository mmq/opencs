"""Bounty accrual and its pushes through the city (I §9.2.12, II §11.2, I §7.2, I App. B)."""

from __future__ import annotations

from fixtures.cities import seat
from fixtures.delivery import deliveries, to
from fixtures.pods import report
from opencs import policy
from opencs.messages import client, server
from opencs.world.city import City, JoinResult

STEP = policy.BOUNTY_PUSH_INTERVAL


def bounty_pushes(c: City, now: float) -> list[tuple[int, server.SetBounty]]:
    return [(p, m) for p, m in deliveries(c, c.tick(now)) if isinstance(m, server.SetBounty)]


def test_a_pod_in_drop_state_accrues_nothing() -> None:
    c, (a, _) = seat([0, 1])
    for t in (1.0, 5.0, 100.0):
        c.client_message(a, report(4), t)  # still dropping
    assert bounty_pushes(c, 200.0) == []


def test_the_bounty_runs_from_leaving_drop_state_and_is_pushed_to_every_holder() -> None:
    c, (a, b, d) = seat([0, 1, 2])
    c.client_message(a, report(1), 10.0)  # falling: out of drop state at t=10
    assert bounty_pushes(c, 10.0 + STEP - 0.1) == []
    pushes = bounty_pushes(c, 10.0 + STEP)
    assert pushes == [(p, server.SetBounty(a, int(STEP))) for p in (a, b, d)]


def test_each_push_carries_the_seconds_alive_at_that_moment() -> None:
    c, (a, b) = seat([0, 1])
    c.client_message(a, report(0), 10.0)
    first = bounty_pushes(c, 10.0 + STEP)
    second = bounty_pushes(c, 10.0 + 2 * STEP + 0.4)  # a late tick
    assert first[0][1].bounty_div10 == int(STEP)
    assert second[0][1].bounty_div10 == int(2 * STEP + 0.4)
    assert {p for p, _ in first} == {a, b}


def test_a_pod_that_has_not_left_drop_is_not_pushed_while_another_is() -> None:
    c, (a, b) = seat([0, 1])
    c.client_message(a, report(0), 10.0)
    pushes = bounty_pushes(c, 10.0 + STEP)
    assert {m.id for _, m in pushes} == {a}
    assert b not in {m.id for _, m in pushes}


def test_a_late_joiners_g_carries_the_bounty_as_it_is_now() -> None:
    c, (a, _) = seat([0, 1])
    c.client_message(a, report(0), 10.0)
    bounty_pushes(c, 10.0 + STEP)
    late = c.join("LATE", 2, 30.0)
    assert isinstance(late, JoinResult)
    out = to(deliveries(c, c.client_message(late.player_id, report(4), 31.0)), late.player_id)
    g = [m for m in out if isinstance(m, server.Introduce) and m.id == a]
    assert [m.bounty_div10 for m in g] == [21]  # a started at 10.0; the 'b' is at 31.0


def test_a_g_for_a_pod_with_no_bounty_yet_carries_zero() -> None:
    c, (a, b) = seat([0, 1], reported=False)
    c.client_message(b, report(4), 1.0)
    out = to(deliveries(c, c.client_message(a, report(4), 2.0)), b)
    assert [m.bounty_div10 for m in out if isinstance(m, server.Introduce)] == [0]


def test_a_death_sends_j_then_zeroes_the_bounty_and_stops_it() -> None:
    c, (a, b) = seat([0, 1])
    c.client_message(a, report(0), 10.0)
    bounty_pushes(c, 10.0 + STEP)  # a's bounty is 10
    out = deliveries(c, c.client_message(a, client.Died(), 25.0))
    assert [m for p, m in out if p == b] == [server.Destroy(a), server.SetBounty(a, 0)]
    assert {p for p, m in out if isinstance(m, server.SetBounty)} == {a, b}
    assert bounty_pushes(c, 100.0) == []  # a dead pod accrues nothing


def test_a_second_j_before_the_respawn_does_not_push_again() -> None:
    c, (a, _) = seat([0, 1])
    c.client_message(a, report(0), 10.0)
    c.client_message(a, client.Died(), 25.0)
    assert c.client_message(a, client.Died(), 26.0) == []


def test_the_next_life_accrues_from_leaving_drop_state_again() -> None:
    c, (a, _) = seat([0, 1])
    c.client_message(a, report(0), 10.0)
    c.client_message(a, client.Died(), 25.0)
    c.client_message(a, client.DropMe(), 34.0)
    c.client_message(a, report(4), 34.1)
    assert bounty_pushes(c, 34.1 + 60.0) == []  # still dropping: nothing accrues
    c.client_message(a, report(1), 40.0)
    assert [m.bounty_div10 for _, m in bounty_pushes(c, 40.0 + STEP)] == [int(STEP)] * 2


def test_a_new_life_re_syncs_the_bounty_to_zero_when_the_pod_leaves_drop_state() -> None:
    """The clients accrue on dead pods too, so the zero sent at the death is stale by the time the
    pod is back: leaving drop state pushes 0 again, to the owner and every holder."""
    c, (a, b) = seat([0, 1])
    c.client_message(a, report(0), 10.0)
    c.client_message(a, client.Died(), 25.0)
    c.client_message(a, client.DropMe(), 34.0)
    dropping = deliveries(c, c.client_message(a, report(4), 34.1))
    assert not [m for _, m in dropping if isinstance(m, server.SetBounty)]
    out = deliveries(c, c.client_message(a, report(1), 40.0))
    assert {p for p, m in out if m == server.SetBounty(a, 0)} == {a, b}
    again = deliveries(c, c.client_message(a, report(1), 40.5))
    assert not [m for _, m in again if isinstance(m, server.SetBounty)]  # once per life
