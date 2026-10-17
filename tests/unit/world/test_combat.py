"""Shot logs, attribution and the hit set (II §9.3, I §9.2)."""

from __future__ import annotations

from opencs import policy
from opencs.world.combat import HitSet, LoggedShot, ShotLog

ME = 3  # the connection's own pod
NPC = 4100


def log_with(*shots: tuple[float, int]) -> ShotLog:
    log = ShotLog(ME)
    for t, shooter in shots:
        log.record(t, shooter)
    return log


def test_a_report_with_no_shot_in_the_log_credits_nobody() -> None:
    result = ShotLog(ME).attribute(10.0)
    assert result.credited is False and result.consumed is None


def test_an_own_pod_shot_a_flight_time_earlier_is_credited_and_consumed() -> None:
    log = log_with((9.0, ME))
    result = log.attribute(10.0)
    assert result.credited is True and result.consumed == LoggedShot(9.0, ME)
    assert len(log) == 0


def test_one_shot_is_consumed_per_report() -> None:
    log = log_with((9.0, ME))
    assert log.attribute(10.0).credited is True
    again = log.attribute(10.0)  # the same shot cannot pay twice
    assert again.credited is False and again.consumed is None


def test_the_window_is_0_to_2_seconds_back_with_both_edges_inside() -> None:
    # now = 10: expected = 9.0, so shots at 8.0 and 10.0 are the last accepted.
    assert log_with((8.0, ME)).attribute(10.0).credited is True
    assert log_with((10.0, ME)).attribute(10.0).credited is True
    assert log_with((7.99, ME)).attribute(10.0).credited is False
    assert log_with((10.01, ME)).attribute(10.0).credited is False


def test_a_shot_outside_the_window_is_not_consumed() -> None:
    log = log_with((10.5, ME), (9.0, ME))
    assert log.attribute(10.0).consumed == LoggedShot(9.0, ME)
    assert log.attribute(11.0).consumed == LoggedShot(10.5, ME)  # still there for a later report


def test_an_own_pod_shot_beats_an_npc_shot_nearer_the_flight_time() -> None:
    # II §9.3: the NPC's shot is exactly on time, the player's is 0.6 s off.
    log = log_with((9.4, ME), (9.0, NPC))
    result = log.attribute(10.0)
    assert result.credited is True and result.consumed == LoggedShot(9.4, ME)
    assert [s.shooter for s in log._shots] == [NPC]  # the NPC's shot is still unconsumed


def test_with_only_npc_shots_in_the_window_the_nearest_is_consumed_and_nobody_credited() -> None:
    log = log_with((8.3, NPC), (9.2, NPC + 1))
    result = log.attribute(10.0)
    assert result.credited is False and result.consumed == LoggedShot(9.2, NPC + 1)
    assert len(log) == 1


def test_a_burst_of_npc_reports_cannot_all_hide_behind_one_own_shot() -> None:
    log = log_with((9.0, ME), (9.0, NPC), (9.0, NPC))
    assert [log.attribute(10.0).credited for _ in range(3)] == [True, False, False]


def test_among_own_shots_the_nearest_the_flight_time_is_used() -> None:
    log = log_with((8.2, ME), (9.1, ME), (9.8, ME))
    assert log.attribute(10.0).consumed == LoggedShot(9.1, ME)


def test_a_shot_by_another_players_pod_is_not_the_connections_own() -> None:
    assert log_with((9.0, ME + 1)).attribute(10.0).credited is False


def test_logs_are_per_connection() -> None:
    mine, theirs = ShotLog(ME), ShotLog(ME + 1)
    mine.record(9.0, ME)
    assert theirs.attribute(10.0).credited is False
    assert mine.attribute(10.0).credited is True


def test_old_shots_are_aged_out_and_the_log_is_capped() -> None:
    log = log_with((0.0, ME))
    log.record(2.01, ME)  # the shot at 0.0 can match no report from now on
    assert len(log) == 1
    for i in range(policy.SHOT_LOG_MAX + 20):
        log.record(5.0 + i * 1e-6, NPC)
    assert len(log) == policy.SHOT_LOG_MAX


def test_the_cap_drops_the_oldest_shots() -> None:
    log = ShotLog(ME)
    for i in range(policy.SHOT_LOG_MAX + 1):
        log.record(5.0 + i * 1e-4, ME + i)
    assert log._shots[0].shooter == ME + 1


def test_the_hit_set_pays_a_hitter_for_exactly_20_seconds() -> None:
    hits = HitSet()
    hits.record(1, 100.0)
    assert hits.payees(120.0) == [1]
    assert hits.payees(120.01) == []


def test_a_second_hit_refreshes_the_time_and_does_not_duplicate() -> None:
    hits = HitSet()
    hits.record(1, 100.0)
    hits.record(1, 115.0)
    assert hits.payees(130.0) == [1]
    hits.record(2, 110.0)
    assert hits.payees(130.0) == [1, 2]


def test_clearing_forgets_every_hitter() -> None:
    hits = HitSet()
    hits.record(1, 100.0)
    hits.clear()
    assert hits.payees(100.0) == []
