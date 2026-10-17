"""A death: payout, kill notice, the ``'J'``, the bounty zero, the mirror's reset (I §9.2.9, II
§11.2, I §9.2.9, II §12)."""

from __future__ import annotations

import logging

import pytest

from fixtures.cities import seat
from fixtures.delivery import deliveries, to
from fixtures.pods import report
from opencs.messages import ServerMessage, client, server
from opencs.world.city import City, Player

KILL = server.KillNotice


def playing(teams: list[int], **config: object) -> tuple[City, list[int]]:
    c, ids = seat(teams, **config)
    for pid in ids:
        c.client_message(pid, report(0), 1.0)  # out of drop state at t=1: bounty runs from 1
    return c, ids


def player(c: City, pid: int) -> Player:
    p = c.player(pid)
    assert p is not None
    return p


def hit(c: City, shooter: int, victim: int, at: float) -> None:
    """``shooter`` fires at ``at - 1`` and reports the hit at ``at``: attributed to it."""
    c.client_message(shooter, client.Shot(shooter, report(0).pos, 0, 0, 1), at - 1.0)
    c.client_message(shooter, client.HitReport(victim, 0), at)


def mult(c: City, pid: int, value: int, at: float) -> None:
    c.client_message(pid, client.Status(0, value, 0), at)


def die(c: City, pid: int, at: float) -> list[tuple[int, ServerMessage]]:
    return deliveries(c, c.client_message(pid, client.Died(), at))


def test_every_non_teammate_in_the_hit_set_is_paid_the_whole_bounty_times_its_own_multiplier() -> (
    None
):
    c, (a, v, d, e) = playing([0, 1, 2, 2])
    mult(c, a, 3, 5.0)
    mult(c, d, 5, 5.0)
    hit(c, a, v, 40.0)
    hit(c, d, v, 41.0)
    die(c, v, 51.0)  # alive since t=1: 50 s, a bounty of 500
    assert player(c, a).score.server_credit == 500 * 3
    assert player(c, d).score.server_credit == 500 * 5  # not divided between payees
    assert player(c, e).score.server_credit == 0  # never hit it


def test_the_payout_uses_the_bounty_at_the_death_not_the_last_push() -> None:
    c, (a, v) = playing([0, 1])
    mult(c, a, 1, 5.0)
    c.tick(31.0)  # a push at 30 s of bounty
    hit(c, a, v, 35.0)
    die(c, v, 38.0)  # 37 s alive
    assert player(c, a).score.server_credit == 370


def test_a_teammate_in_the_hit_set_is_not_paid_and_gets_no_notice() -> None:
    c, (v, mate, other) = playing([1, 1, 0])
    hit(c, mate, v, 40.0)
    hit(c, other, v, 41.0)
    out = die(c, v, 50.0)
    assert player(c, mate).score.server_credit == 0
    assert not [m for m in to(out, mate) if isinstance(m, KILL)]
    assert [m for m in to(out, other) if isinstance(m, KILL)]


def test_a_hit_counts_for_exactly_20_seconds() -> None:
    c, (a, v) = playing([0, 1])
    hit(c, a, v, 40.0)
    die(c, v, 60.0)  # exactly 20 s later: still counts
    assert player(c, a).score.server_credit > 0
    c2, (a2, v2) = playing([0, 1])
    hit(c2, a2, v2, 40.0)
    die(c2, v2, 60.01)
    assert player(c2, a2).score.server_credit == 0


def test_a_victim_nobody_hit_pays_nobody() -> None:
    c, (a, v) = playing([0, 1])
    out = die(c, v, 30.0)
    assert not [m for _, m in out if isinstance(m, KILL)]
    assert player(c, a).score.server_credit == 0


def test_the_wire_order_is_notice_then_j_then_the_bounty_zero() -> None:
    c, (a, v, o) = playing([0, 1, 2])
    hit(c, a, v, 40.0)
    out = die(c, v, 50.0)
    assert to(out, a) == [KILL(True), server.Destroy(v), server.SetBounty(v, 0)]
    assert to(out, v) == [server.Destroy(v), server.SetBounty(v, 0)]  # the dying client too
    assert to(out, o) == [server.Destroy(v), server.SetBounty(v, 0)]


def test_the_notice_goes_to_the_payee_alone() -> None:
    c, (a, v, _) = playing([0, 1, 2])
    hit(c, a, v, 40.0)
    out = die(c, v, 50.0)
    assert [pid for pid, m in out if isinstance(m, KILL)] == [a]


def test_the_kill_is_earned_when_the_victims_highest_bay_reaches_the_payees_lowest_minus_one() -> (
    None
):
    c, (a, v) = playing([0, 1])
    c.client_message(a, client.Bays((3, 3, 3, 3, 3, 3, 3, 3, 3), 0), 5.0)
    c.client_message(v, client.Bays((2, 0, 0, 0, 0, 0, 0, 0, 0), 0), 5.0)
    hit(c, a, v, 40.0)
    assert to(die(c, v, 50.0), a)[0] == KILL(True)  # 2 >= 3 - 1
    assert player(c, a).score.marker == 1  # mirrored: the client adds one on the notice


def test_a_kill_below_that_is_recorded_but_not_earned() -> None:
    c, (a, v) = playing([0, 1])
    c.client_message(a, client.Bays((3, 3, 3, 3, 3, 3, 3, 3, 3), 0), 5.0)
    c.client_message(v, client.Bays((1, 1, 1, 1, 1, 1, 1, 1, 1), 0), 5.0)
    hit(c, a, v, 40.0)
    assert to(die(c, v, 50.0), a)[0] == KILL(False)
    assert player(c, a).score.marker == 0


def test_a_new_players_stock_bays_make_a_kill_on_another_new_player_earned() -> None:
    c, (a, v) = playing([0, 1])
    hit(c, a, v, 40.0)
    assert to(die(c, v, 50.0), a)[0] == KILL(True)  # 1 >= 0 - 1: empty bays count as lowest


def test_the_victims_mirror_takes_the_clients_reset() -> None:
    c, (_, v) = playing([0, 1])
    mult(c, v, 6, 5.0)
    c.client_message(v, client.Bays((1, 1, 1, 1, 0, 0, 0, 0, 0), 2), 5.0)
    die(c, v, 50.0)
    score = player(c, v).score
    assert (score.multiplier, score.marker) == (1, 1)


def test_the_hit_set_is_cleared_by_the_death() -> None:
    c, (a, v) = playing([0, 1])
    hit(c, a, v, 40.0)
    die(c, v, 50.0)
    c.client_message(v, client.DropMe(), 58.0)
    c.client_message(v, report(0), 59.0)
    out = die(c, v, 60.0)  # a's old hit is within 20 s of this death, but was spent
    assert not [m for _, m in out if isinstance(m, KILL)]


def test_a_second_j_pays_and_notifies_nobody_again() -> None:
    c, (a, v) = playing([0, 1])
    mult(c, a, 1, 5.0)
    hit(c, a, v, 40.0)
    die(c, v, 50.0)
    assert die(c, v, 51.0) == []
    assert player(c, a).score.server_credit == 490


def test_a_payee_who_left_forfeits() -> None:
    c, (a, v) = playing([0, 1])
    hit(c, a, v, 40.0)
    c.leave(a, 45.0)
    out = die(c, v, 50.0)
    assert [m for _, m in out if isinstance(m, KILL)] == []


def test_the_payout_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    c, (a, v) = playing([0, 1])
    mult(c, a, 2, 5.0)
    hit(c, a, v, 40.0)
    with caplog.at_level(logging.INFO, logger="opencs.world"):
        die(c, v, 51.0)
    assert any("paid 1000 (bounty 500 x 2)" in r.getMessage() for r in caplog.records)


def reported_status(c: City, pid: int, at: float, **kw: int) -> list[server.SetStatus]:
    msg = client.Status(kw.get("score", 100), kw.get("multiplier", 1), kw.get("bay_points", 5))
    out = deliveries(c, c.client_message(pid, msg, at))
    return [m for _, m in out if isinstance(m, server.SetStatus)]


def test_after_a_death_the_first_report_2_seconds_on_is_raised_to_the_floor() -> None:
    c, (a, b) = playing([0, 1], max_module_level=4, max_multiplier=20)
    die(c, a, 10.0)
    assert reported_status(c, a, 11.0, score=777) == []  # too soon: may predate the reset
    out = reported_status(c, a, 12.0, score=900, bay_points=33)
    assert out == [server.SetStatus(900, 4, 33)]  # that report's own SCORE and bay points
    assert reported_status(c, a, 13.0) == []  # once per death
    assert reported_status(c, b, 20.0) == []  # b did not die
