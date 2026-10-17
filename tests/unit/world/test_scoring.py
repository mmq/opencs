"""Bounty accrual, the score mirror, the multiplier floor, earned kills and payouts (I §9.2.12, II
§11.2, II §11.1, I §9.2.9, II §11.2, II §12)."""

from __future__ import annotations

import pytest

from opencs import policy
from opencs.messages import client, server
from opencs.world.events import Event, ToHolders
from opencs.world.scoring import (
    Bounty,
    PlayerScore,
    is_earned_kill,
    multiplier_floor,
    payout,
)

POD = 7


def push(value: int) -> Event:
    return Event(server.SetBounty(POD, value), ToHolders(POD))


# --- bounty -------------------------------------------------------------------------------------


def test_a_bounty_that_has_not_started_is_zero_and_pushes_nothing() -> None:
    b = Bounty(POD)
    assert b.value(100.0) == 0 and not b.running
    assert b.tick(100.0) == []


def test_the_bounty_is_one_wire_unit_per_second_from_the_start() -> None:
    b = Bounty(POD)
    b.start(100.0)
    assert [b.value(t) for t in (100.0, 100.99, 101.0, 150.5, 400.0)] == [0, 0, 1, 50, 300]


def test_starting_again_does_not_restart_the_clock() -> None:
    b = Bounty(POD)
    b.start(100.0)
    b.start(130.0)
    assert b.value(140.0) == 40


def test_pushes_come_every_interval_with_the_value_at_that_moment() -> None:
    b = Bounty(POD)
    b.start(100.0)
    step = policy.BOUNTY_PUSH_INTERVAL
    assert b.tick(100.0 + step - 0.01) == []
    assert b.tick(100.0 + step) == [push(int(step))]
    assert b.shown == int(step)
    assert b.tick(100.0 + step + 1.0) == []  # not again until another interval
    assert b.tick(100.0 + 2 * step) == [push(int(2 * step))]


def test_a_late_tick_pushes_once_not_once_per_missed_interval() -> None:
    b = Bounty(POD)
    b.start(0.0)
    assert len(b.tick(95.0)) == 1
    assert b.tick(95.5) == []


def test_zero_stops_the_clock_pushes_zero_and_resets_what_a_g_carries() -> None:
    b = Bounty(POD)
    b.start(0.0)
    b.tick(12.0)
    assert b.shown == 12
    assert b.zero() == [push(0)]
    assert b.shown == 0 and b.value(50.0) == 0 and b.tick(50.0) == []
    b.start(60.0)  # the next life accrues from scratch
    assert b.value(65.0) == 5


# --- the floor ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("module_level", "max_mult", "floor"),
    [(0, 8, 1), (1, 8, 1), (4, 20, 4), (5, 3, 3), (2, 8, 2)],
)
def test_the_floor_is_the_module_level_at_least_1_and_at_most_the_cap(
    module_level: int, max_mult: int, floor: int
) -> None:
    assert multiplier_floor(module_level, max_mult) == floor


def test_a_new_player_starts_with_the_bays_then_the_status_at_the_floor() -> None:
    s = PlayerScore()
    bays, status = s.start_message(floor=4)
    assert bays == server.SetBays((1, 1, 1, 1, 0, 0, 0, 0, 0), 0)
    assert status == server.SetStatus(0, 4, 0)
    assert s.multiplier == 4


def status(score: int = 900, mult: int = 1, points: int = 77) -> client.Status:
    return client.Status(score, mult, points)


def test_a_report_is_mirrored_and_needs_no_answer_when_nothing_died() -> None:
    s = PlayerScore()
    assert s.on_status(status(1234, 3, 56), 10.0, floor=4, max_multiplier=8) == []
    assert (s.score, s.multiplier, s.bay_points) == (1234, 3, 56)  # below the floor: not our job


def test_the_first_report_two_seconds_after_a_death_is_raised_to_the_floor() -> None:
    s = PlayerScore(multiplier=6)
    s.on_own_death(100.0)
    assert s.multiplier == 1
    out = s.on_status(status(900, 1, 77), 100.0 + policy.FLOOR_SETTLE, floor=4, max_multiplier=8)
    assert out == [server.SetStatus(900, 4, 77)]  # that report's own SCORE and bay points
    assert s.multiplier == 4


def test_a_report_just_before_the_settle_time_gets_nothing_and_does_not_use_up_the_floor() -> None:
    s = PlayerScore()
    s.on_own_death(100.0)
    assert s.on_status(status(5, 1, 6), 101.99, floor=4, max_multiplier=8) == []
    out = s.on_status(status(9, 1, 8), 102.0, floor=4, max_multiplier=8)
    assert out == [server.SetStatus(9, 4, 8)]


def test_an_early_reports_stale_multiplier_does_not_replace_the_reset() -> None:
    s = PlayerScore()
    s.on_own_death(100.0)
    s.on_status(status(5, 9, 6), 100.5, floor=4, max_multiplier=20)  # built before the reset
    assert s.multiplier == 1
    assert (s.score, s.bay_points) == (5, 6)


def test_the_floor_is_pushed_once_per_death() -> None:
    s = PlayerScore()
    s.on_own_death(100.0)
    assert len(s.on_status(status(), 103.0, floor=4, max_multiplier=8)) == 1
    assert s.on_status(status(), 104.0, floor=4, max_multiplier=8) == []
    s.on_own_death(200.0)  # a new death earns a new floor
    assert len(s.on_status(status(), 203.0, floor=4, max_multiplier=8)) == 1


def test_a_report_already_at_the_floor_gets_no_push_and_uses_up_the_death() -> None:
    s = PlayerScore()
    s.on_own_death(100.0)
    assert s.on_status(status(mult=4), 103.0, floor=4, max_multiplier=8) == []
    assert s.on_status(status(mult=1), 105.0, floor=4, max_multiplier=8) == []  # no second chance


def test_a_reported_multiplier_is_capped_at_the_citys_maximum() -> None:
    s = PlayerScore()
    s.on_status(status(mult=200), 10.0, floor=1, max_multiplier=8)
    assert s.multiplier == 8


def test_a_death_resets_the_multiplier_and_lowers_the_marker() -> None:
    s = PlayerScore(multiplier=6, marker=2)
    s.on_own_death(50.0)
    assert (s.multiplier, s.marker) == (1, 1)
    s.on_own_death(80.0)
    assert s.marker == 0


# --- bays and earned kills ----------------------------------------------------------------------


def test_the_bay_report_is_mirrored() -> None:
    s = PlayerScore()
    assert s.on_bays(client.Bays((2, 2, 1, 1, 1, 0, 0, 0, 0), 0)) is True
    assert s.bays == (2, 2, 1, 1, 1, 0, 0, 0, 0)


def test_a_bay_level_past_the_clients_table_is_refused() -> None:
    s = PlayerScore()
    assert s.on_bays(client.Bays((6, 1, 1, 1, 0, 0, 0, 0, 0), 0)) is False
    assert s.bays == policy.NEW_PLAYER_BAYS


def bays(*levels: int) -> PlayerScore:
    return PlayerScore(bays=levels)


def test_empty_bays_count_as_the_lowest() -> None:
    assert PlayerScore().lowest_bay == 0 and PlayerScore().highest_bay == 1


@pytest.mark.parametrize(
    ("victim", "payee", "earned"),
    [
        ((1, 1, 1, 1, 0, 0, 0, 0, 0), (1, 1, 1, 1, 0, 0, 0, 0, 0), True),  # 1 >= 0 - 1
        ((2, 1, 1, 1, 1, 1, 1, 1, 1), (3, 3, 3, 3, 3, 3, 3, 3, 3), True),  # 2 >= 2
        ((1, 1, 1, 1, 1, 1, 1, 1, 1), (4, 4, 4, 4, 4, 4, 4, 4, 4), False),  # 1 >= 3 no
    ],
)
def test_an_earned_kill_compares_the_victims_highest_bay_with_the_payees_lowest(
    victim: tuple[int, ...], payee: tuple[int, ...], earned: bool
) -> None:
    assert is_earned_kill(bays(*victim), bays(*payee)) is earned


def test_the_earned_boundary_is_exactly_one_below_the_payees_lowest() -> None:
    payee = bays(3, 3, 3, 3, 3, 3, 3, 3, 3)
    assert is_earned_kill(bays(2, 0, 0, 0, 0, 0, 0, 0, 0), payee) is True  # 2 >= 3 - 1
    assert is_earned_kill(bays(1, 1, 1, 1, 1, 1, 1, 1, 1), payee) is False  # 1 < 2


# --- payout -------------------------------------------------------------------------------------


def test_the_payout_is_the_entire_bounty_in_points_times_the_payees_multiplier() -> None:
    p = payout(payee=2, bounty_units=50, multiplier=3)
    assert (p.bounty, p.points) == (500, 1500)  # 50 wire units display as 500


def test_each_payee_is_paid_its_own_multiplier_from_the_same_bounty() -> None:
    assert [payout(i, 40, m).points for i, m in ((1, 1), (2, 5))] == [400, 2000]
