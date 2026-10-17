"""One pod's life: the drop, the death handshake and the give-up (I §7.1, I §7.5, II §7, I §7.8)."""

from __future__ import annotations

from fixtures.pods import report
from opencs import policy
from opencs.messages import server
from opencs.world.events import Event, ToAll, ToHolders
from opencs.world.pods import Pod, PodPhase

RELEASE = Event(server.ReleaseDrop(5), ToHolders(5))
DEATH = Event(server.Destroy(5), ToAll())


def test_the_first_state_4_report_is_answered_with_a_drop_to_the_holders() -> None:
    pod = Pod(5)
    assert pod.phase is PodPhase.AWAIT_FIRST_FRAME
    assert pod.on_report(report(4), 0.0) == [RELEASE]
    assert pod.phase is PodPhase.DROPPING


def test_ticks_repeat_the_drop_while_the_pod_still_reports_state_4() -> None:
    pod = Pod(5)
    pod.on_report(report(4), 0.0)
    assert pod.tick(0.2) == [RELEASE]
    assert pod.tick(0.4) == [RELEASE]


def test_nothing_is_sent_before_the_first_report_or_once_the_pod_has_left_drop_state() -> None:
    pod = Pod(5)
    assert pod.tick(0.0) == []
    pod.on_report(report(4), 0.1)
    assert pod.on_report(report(1), 0.5) == []  # falling
    assert pod.phase is PodPhase.IN_PLAY
    assert pod.tick(0.6) == []
    for state in (2, 0):
        assert pod.on_report(report(state), 1.0) == []
        assert pod.tick(1.2) == []


def test_a_first_report_that_is_not_state_4_is_in_play_without_an_answer() -> None:
    pod = Pod(5)
    assert pod.on_report(report(0), 0.0) == []
    assert pod.phase is PodPhase.IN_PLAY


def test_the_last_report_is_kept() -> None:
    pod = Pod(5)
    first, second = report(4), report(1, y=229)
    pod.on_report(first, 0.0)
    pod.on_report(second, 0.5)
    assert pod.last == second
    assert pod.reported_state == 1


def test_a_bare_j_sends_j_to_every_client_and_kills_the_pod() -> None:
    pod = Pod(5)
    pod.on_report(report(0), 0.0)
    assert pod.on_death(10.0) == [DEATH]
    assert pod.phase is PodPhase.DEAD


def test_a_second_j_before_the_respawn_is_ignored() -> None:
    pod = Pod(5)
    pod.on_report(report(0), 0.0)
    pod.on_death(10.0)
    assert pod.on_death(10.5) == []


def test_state_4_reports_during_the_fly_over_get_no_drop_neither_on_report_nor_on_tick() -> None:
    pod = Pod(5)
    pod.on_report(report(0), 0.0)
    pod.on_death(10.0)
    assert pod.on_report(report(4), 11.2) == []
    assert pod.tick(11.4) == []
    assert pod.tick(15.0) == []
    assert pod.phase is PodPhase.DEAD


def test_the_bare_a_after_a_death_is_answered_at_once_and_drops_the_pod_again() -> None:
    pod = Pod(5)
    pod.on_report(report(0), 0.0)
    pod.on_death(10.0)
    pod.on_report(report(4), 11.2)
    assert pod.on_drop_request(19.0) == [RELEASE]
    assert pod.phase is PodPhase.DROPPING
    assert pod.tick(19.2) == [RELEASE]  # the fly-over's last report still said 4
    assert pod.on_report(report(1), 19.5) == []
    assert pod.phase is PodPhase.IN_PLAY
    assert pod.tick(19.6) == []


def test_a_pod_that_has_not_died_ignores_a_bare_a() -> None:
    pod = Pod(5)
    assert pod.on_drop_request(0.0) == []  # nothing reported yet
    pod.on_report(report(0), 0.1)
    assert pod.on_drop_request(0.2) == []
    assert pod.phase is PodPhase.IN_PLAY


def test_the_pod_can_die_and_respawn_again() -> None:
    pod = Pod(5)
    pod.on_report(report(0), 0.0)
    for t in (10.0, 40.0):
        assert pod.on_death(t) == [DEATH]
        assert pod.on_drop_request(t + 9.0) == [RELEASE]
        pod.on_report(report(0), t + 11.0)


def test_with_no_bare_a_the_pod_answers_state_4_reports_again_after_the_timeout() -> None:
    pod = Pod(5)
    pod.on_report(report(0), 0.0)
    pod.on_death(10.0)
    just_before = 10.0 + policy.DROP_REQUEST_TIMEOUT - 0.1
    assert pod.on_report(report(4), just_before) == []  # the last moment still dead
    assert pod.on_report(report(4), 10.0 + policy.DROP_REQUEST_TIMEOUT) == [RELEASE]
    assert pod.phase is PodPhase.DROPPING


def test_the_timeout_is_also_noticed_by_a_tick() -> None:
    pod = Pod(5)
    pod.on_report(report(4), 0.0)
    pod.on_report(report(0), 0.5)
    pod.on_death(10.0)
    pod.on_report(report(4), 11.0)  # recorded, not answered
    assert pod.tick(10.0 + policy.DROP_REQUEST_TIMEOUT - 0.1) == []
    assert pod.tick(10.0 + policy.DROP_REQUEST_TIMEOUT) == [RELEASE]


def test_a_late_bare_a_after_the_timeout_is_still_answered() -> None:
    pod = Pod(5)
    pod.on_report(report(0), 0.0)
    pod.on_death(10.0)
    assert pod.on_drop_request(10.0 + policy.DROP_REQUEST_TIMEOUT + 5) == [RELEASE]


def test_after_the_bare_a_ticks_stay_quiet_when_the_last_report_was_not_state_4() -> None:
    """I §7.1, II §7: 'A' repeats only for as long as the reports still say 4."""
    pod = Pod(5)
    pod.on_report(report(0), 0.0)
    pod.on_death(1.0)
    assert pod.on_drop_request(2.0) == [RELEASE]  # answered once
    assert pod.tick(2.2) == []  # the last report said 0, not 4


def test_after_the_give_up_ticks_stay_quiet_when_the_last_report_was_not_state_4() -> None:
    pod = Pod(5)
    pod.on_report(report(0), 0.0)
    pod.on_death(1.0)
    assert pod.tick(1.0 + policy.DROP_REQUEST_TIMEOUT) == []
