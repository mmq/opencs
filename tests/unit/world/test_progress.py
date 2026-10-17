"""The save rules: what a client's reports change in the record (II §11.3)."""

from __future__ import annotations

import pytest

from opencs import policy
from opencs.wire.codec import BIAS88_MAX, BIAS88_MIN
from opencs.world.progress import NEW_PROGRESS, Progress, SavedProgress

BAYS = (3, 2, 1, 1, 5, 4, 0, 1, 2)
STORED = SavedProgress(bays=BAYS, marker=-2, bay_points=957, score=1670, career=500)


def restored(stored: SavedProgress | None = STORED, at: float = 100.0) -> Progress:
    p = Progress(stored)
    p.restored(at)
    return p


def test_a_new_identity_starts_from_the_defaults_and_has_nothing_to_save() -> None:
    p = Progress(None)
    assert p.record == NEW_PROGRESS
    assert p.record.bays == policy.NEW_PLAYER_BAYS and p.record.marker == policy.NEW_PLAYER_MARKER
    assert p.take_unsaved() is None


# --- rule 7: SCORE is per game ------------------------------------------------------------------


def test_a_new_game_adds_the_last_score_to_the_career_and_starts_at_zero() -> None:
    p = Progress(STORED)
    assert (p.record.score, p.record.career) == (0, 500 + 1670)
    assert (p.record.bays, p.record.marker, p.record.bay_points) == (BAYS, -2, 957)  # kept
    assert p.take_unsaved() == p.record  # the roll is a change to save
    assert p.take_unsaved() is None


def test_a_record_with_no_score_to_roll_is_not_marked_changed() -> None:
    p = Progress(SavedProgress(BAYS, 0, 10, 0, 7))
    assert p.record.career == 7 and p.take_unsaved() is None


# --- rule 1: what a status report saves ---------------------------------------------------------


def test_a_status_report_saves_score_and_bay_points_but_has_no_multiplier_to_save() -> None:
    p = restored()
    p.take_unsaved()
    p.on_status(40, 1_000, 101.0)
    assert (p.record.score, p.record.bay_points) == (40, 1_000)
    assert p.take_unsaved() == p.record
    assert "multiplier" not in SavedProgress.__slots__


def test_a_bay_report_saves_the_bays_and_the_marker() -> None:
    p = restored()
    p.on_bays((3, 2, 1, 1, 5, 4, 0, 1, 3), 4, p.record.marker)
    assert (p.record.bays, p.record.marker) == ((3, 2, 1, 1, 5, 4, 0, 1, 3), 4)


def test_a_bay_report_the_client_cannot_hold_is_ignored() -> None:
    p = restored()
    p.take_unsaved()
    p.on_bays((6, 2, 1, 1, 5, 4, 0, 1, 2), 0, p.record.marker)
    p.on_bays((1, 1, 1), 0, p.record.marker)
    assert p.record.bays == BAYS and p.take_unsaved() is None


def test_a_reported_marker_is_stored_within_the_b88_field() -> None:
    p = restored()
    p.on_bays(BAYS, BIAS88_MAX + 50, p.record.marker)
    assert p.record.marker == BIAS88_MAX
    p.on_own_death(BIAS88_MIN - 50)
    assert p.record.marker == BIAS88_MIN


def test_negative_bay_points_are_not_saved() -> None:
    p = restored(SavedProgress(BAYS, 0, 0, 0, 0))
    p.on_status(5, -3, 101.0)
    assert (p.record.score, p.record.bay_points) == (0, 0)


# --- rule 2: the SCORE floor --------------------------------------------------------------------


def test_a_score_below_the_last_one_saved_is_refused_and_an_equal_one_accepted() -> None:
    p = restored(SavedProgress(BAYS, 0, 0, 0, 0))
    p.on_status(500, 20, 101.0)
    p.on_status(499, 30, 102.0)  # below: refused, and its bay points go with it
    assert (p.record.score, p.record.bay_points) == (500, 20)
    p.on_status(500, 31, 103.0)  # equal: saved
    assert (p.record.score, p.record.bay_points) == (500, 31)
    p.on_status(0, 0, 104.0)
    assert p.record.score == 500


def test_the_floor_is_the_last_score_saved_this_game_not_the_previous_games() -> None:
    p = restored()  # last game 1670, rolled into the career: this game starts at 0
    p.on_status(12, 957, 101.0)
    assert p.record.score == 12


# --- rule 3: the restore guard ------------------------------------------------------------------


def test_a_stale_report_after_a_restore_is_held() -> None:
    p = restored()
    p.take_unsaved()
    p.on_status(0, 0, 101.0)  # built before the restore landed
    assert p.record.bay_points == 957 and p.take_unsaved() is None


def test_the_guard_releases_on_a_report_that_shows_the_restored_values() -> None:
    p = restored()
    p.on_status(0, 0, 101.0)  # held
    p.on_status(0, 957, 102.0)  # shows SCORE 0 and 957 points: released and saved
    assert p.record.bay_points == 957
    p.on_status(10, 12, 103.0)  # the guard is off: lower points now save
    assert (p.record.score, p.record.bay_points) == (10, 12)


@pytest.mark.parametrize("score,points", [(0, 956), (0, 0)])
def test_either_value_short_of_the_restore_keeps_the_hold(score: int, points: int) -> None:
    p = restored()
    p.on_status(score, points, 101.0)
    assert p.record.bay_points == 957


def test_the_guard_times_out_after_the_settle_and_not_before() -> None:
    held = restored(at=100.0)
    held.on_status(0, 0, 100.0 + policy.RESTORE_SETTLE - 0.001)
    assert held.record.bay_points == 957
    freed = restored(at=100.0)
    freed.on_status(0, 0, 100.0 + policy.RESTORE_SETTLE)
    assert freed.record.bay_points == 0


def test_without_a_restore_there_is_no_guard() -> None:
    p = Progress(STORED)  # the status has not been sent yet
    p.on_status(0, 3, 1.0)
    assert p.record.bay_points == 3


# --- rule 4: own death --------------------------------------------------------------------------


def test_the_marker_after_the_clients_silent_minus_one_is_saved() -> None:
    p = restored()
    p.take_unsaved()
    p.on_own_death(-3)
    assert p.record.marker == -3 and p.take_unsaved() == p.record


# --- rule 5: the points-upgrade guard -----------------------------------------------------------


@pytest.mark.parametrize("level", range(5))
def test_a_bay_bought_with_points_spends_its_threshold_taken_before_the_upgrade(
    level: int,
) -> None:
    threshold = policy.BAY_UPGRADE_POINTS[level]
    bays = (*([level] + [1] * 8),)
    p = restored(SavedProgress(bays, 0, threshold * 3 + 5, 0, 0))
    p.on_bays((level + 1, *bays[1:]), 0, p.record.marker)
    assert p.record.bay_points == threshold * 2 + 5
    assert p.record.bays[0] == level + 1


def test_the_thresholds_are_the_manuals_six_powers_of_ten() -> None:
    assert policy.BAY_UPGRADE_POINTS == (1_000, 10_000, 100_000, 1_000_000, 10_000_000, 10**8)


def test_the_common_case_floors_at_zero() -> None:
    p = restored(SavedProgress((1, 1, 1, 1, 0, 0, 0, 0, 0), 0, 957, 0, 0))
    p.on_bays(
        (1, 1, 1, 1, 1, 0, 0, 0, 0), 0, p.record.marker
    )  # the empty bay 5 filled: threshold 1000 > 957
    assert p.record.bay_points == 0


def test_an_upgrade_by_battle_marker_does_not_touch_the_points() -> None:
    p = restored(SavedProgress((1, 1, 1, 1, 0, 0, 0, 0, 0), 1, 957, 0, 0))
    p.on_bays((1, 1, 1, 1, 1, 0, 0, 0, 0), 0, p.record.marker)  # the marker went 1 -> 0 with it
    assert p.record.bay_points == 957


def test_two_bays_or_two_levels_at_once_is_not_a_points_purchase() -> None:
    bays = (1, 1, 1, 1, 0, 0, 0, 0, 0)
    two_bays = restored(SavedProgress(bays, 0, 5_000, 0, 0))
    two_bays.on_bays((2, 2, 1, 1, 0, 0, 0, 0, 0), 0, two_bays.record.marker)
    two_levels = restored(SavedProgress(bays, 0, 5_000, 0, 0))
    two_levels.on_bays((3, 1, 1, 1, 0, 0, 0, 0, 0), 0, two_levels.record.marker)
    assert two_bays.record.bay_points == 5_000 and two_levels.record.bay_points == 5_000


def test_a_downgrade_is_not_a_points_purchase() -> None:
    p = restored(SavedProgress((2, 1, 1, 1, 0, 0, 0, 0, 0), 0, 500, 0, 0))
    p.on_bays((1, 1, 1, 1, 0, 0, 0, 0, 0), 0, p.record.marker)
    assert p.record.bay_points == 500


def test_a_purchase_while_the_restore_guard_is_up_does_not_hold_the_next_report() -> None:
    p = restored(SavedProgress((1, 1, 1, 1, 0, 0, 0, 0, 0), 0, 990, 0, 0))
    p.on_bays((1, 1, 1, 1, 1, 0, 0, 0, 0), 0, p.record.marker)  # saved points now 0
    p.on_status(0, 40, 101.0)  # the client's real points, 40 < the 990 it was restored with
    assert p.record.bay_points == 40
