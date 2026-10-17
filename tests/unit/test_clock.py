import pytest

from opencs.clock import Clock, ManualClock, MonotonicClock


def test_manual_clock_starts_where_told_and_advances() -> None:
    clock = ManualClock(5.0)
    assert clock.now() == 5.0
    clock.advance(2.5)
    assert clock.now() == 7.5


def test_manual_clock_set_moves_forward() -> None:
    clock = ManualClock()
    clock.set(10.0)
    assert clock.now() == 10.0


def test_manual_clock_refuses_to_go_backward() -> None:
    clock = ManualClock(3.0)
    with pytest.raises(ValueError):
        clock.advance(-0.1)
    with pytest.raises(ValueError):
        clock.set(2.9)
    assert clock.now() == 3.0


def test_monotonic_clock_never_decreases() -> None:
    clock: Clock = MonotonicClock()
    readings = [clock.now() for _ in range(100)]
    assert readings == sorted(readings)
