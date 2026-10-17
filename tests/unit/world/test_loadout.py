"""The installed-module ledger: pending until the weight shows it (I §9.3.4, II §13)."""

from __future__ import annotations

import pytest

from opencs import policy
from opencs.world.loadout import Loadout, weight_of

HIGH_SPEED, HIGH_JUMP, ROCKET_PACK, MEGA_TOWER, PERFORMANCE = 1, 2, 5, 21, 35  # weights 1 1 4 8 4
GRACE = policy.LOADOUT_GRACE_REPORTS


def test_the_catalog_weights_are_summed() -> None:
    assert weight_of([HIGH_SPEED, ROCKET_PACK, MEGA_TOWER]) == 1 + 4 + 8
    assert weight_of([]) == 0


def test_an_install_is_pending_until_a_weight_shows_it() -> None:
    led = Loadout()
    led.install(ROCKET_PACK)
    assert (led.modules, led.pending) == ([], [ROCKET_PACK])
    assert led.report(4) is None
    assert (led.modules, led.pending) == ([ROCKET_PACK], [])


def test_several_pending_installs_are_confirmed_oldest_first() -> None:
    led = Loadout()
    led.install(MEGA_TOWER)
    led.install(HIGH_SPEED)
    assert led.report(8) is None  # only the first landed
    assert (led.modules, led.pending) == ([MEGA_TOWER], [HIGH_SPEED])
    assert led.report(9) is None
    assert (led.modules, led.pending) == ([MEGA_TOWER, HIGH_SPEED], [])


def test_an_install_that_never_landed_is_dropped_after_reports_match_without_it() -> None:
    led = Loadout()
    led.install(ROCKET_PACK)
    for _ in range(GRACE):  # these reports match the ledger without it, and it waits
        assert led.report(0) is None
        assert led.pending == [ROCKET_PACK]
    assert led.report(0) is None  # the next one gives up on it
    assert (led.pending, led.modules, led.failed) == ([], [], [ROCKET_PACK])
    assert led.mismatches == 0


def test_trusting_every_install_would_disagree_with_the_client() -> None:
    # What the "trust every install" break variant does: weight 0 against a confirmed Rocket Pack.
    led = Loadout()
    led.modules.append(ROCKET_PACK)
    results = [led.report(0) for _ in range(GRACE + 1)]
    assert results[:GRACE] == [None] * GRACE and results[GRACE] is not None


def test_a_removal_takes_a_confirmed_module_out_and_reports_whether_it_was_there() -> None:
    led = Loadout()
    led.install(ROCKET_PACK)
    led.install(HIGH_SPEED)
    led.report(5)
    assert led.remove(ROCKET_PACK) is True
    assert led.modules == [HIGH_SPEED]
    assert led.report(1) is None
    assert led.remove(MEGA_TOWER) is False
    assert led.modules == [HIGH_SPEED]


def test_removal_prefers_a_confirmed_copy_over_a_pending_one() -> None:
    led = Loadout()
    led.install(HIGH_SPEED)
    led.report(1)
    led.install(HIGH_SPEED)  # pending second copy
    assert led.remove(HIGH_SPEED) is True
    assert (led.modules, led.pending) == ([], [HIGH_SPEED])


def test_a_pending_install_can_be_removed_too() -> None:
    led = Loadout()
    led.install(HIGH_SPEED)
    assert led.remove(HIGH_SPEED) is True
    assert led.carried == []


def test_two_of_one_module_are_legal() -> None:
    led = Loadout()
    led.install(HIGH_SPEED)
    led.install(HIGH_SPEED)
    assert led.report(2) is None and led.modules == [HIGH_SPEED, HIGH_SPEED]
    led.remove(HIGH_SPEED)
    assert led.report(1) is None


def test_death_clears_everything_confirmed_and_pending() -> None:
    led = Loadout()
    led.install(ROCKET_PACK)
    led.report(4)
    led.install(MEGA_TOWER)
    led.clear()
    assert led.carried == []
    assert led.report(0) is None


@pytest.mark.parametrize("active", [HIGH_JUMP, PERFORMANCE])
def test_a_hundred_more_is_expected_only_with_high_jump_or_performance(active: int) -> None:
    led = Loadout()
    led.install(active)
    expected = weight_of([active])
    assert led.report(expected + 100) is None and led.modules == [active]
    assert led.report(expected) is None  # inactive again


def test_a_hundred_more_without_such_a_module_is_a_disagreement() -> None:
    led = Loadout()
    led.install(HIGH_SPEED)
    led.report(1)
    results = [led.report(101) for _ in range(GRACE + 1)]
    assert results[:GRACE] == [None] * GRACE and results[GRACE] is not None


def test_a_hundred_more_with_a_pending_high_jump_confirms_it() -> None:
    led = Loadout()
    led.install(HIGH_JUMP)
    assert led.report(101) is None
    assert led.modules == [HIGH_JUMP]


def test_a_disagreement_is_tolerated_for_the_grace_then_described_and_counted() -> None:
    led = Loadout()
    led.install(ROCKET_PACK)
    led.report(4)
    for _ in range(GRACE):
        assert led.report(9) is None
    text = led.report(9)
    assert text is not None and "client weight 9" in text and "ledger 4" in text
    assert led.mismatches == 1 and led.disagreeing == GRACE + 1
    assert led.report(9) is not None and led.mismatches == 2
    assert led.report(4) is None and led.disagreeing == 0  # a match ends the episode


def test_a_change_restarts_the_grace() -> None:
    led = Loadout()
    led.install(HIGH_SPEED)
    led.report(1)
    for _ in range(GRACE):
        led.report(5)
    led.remove(HIGH_SPEED)  # the client's report can lag the change by a frame
    assert led.report(1) is None and led.report(1) is None
    assert led.mismatches == 0
