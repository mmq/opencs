from opencs.session.badinput import WindowedCounter


def test_exceeds_only_above_the_limit() -> None:
    counter = WindowedCounter(3, 10.0)
    assert not counter.record(0.0, 3)
    assert counter.record(0.0)
    assert counter.total == 4


def test_old_events_leave_the_window_at_exactly_its_length() -> None:
    counter = WindowedCounter(2, 10.0)
    counter.record(0.0, 2)
    assert counter.record(9.99)  # the first two are still inside
    counter = WindowedCounter(2, 10.0)
    counter.record(0.0, 2)
    assert not counter.record(10.0)  # 10 s on, they are out
    assert counter.total == 1


def test_a_count_of_zero_records_nothing() -> None:
    counter = WindowedCounter(0, 1.0)
    assert not counter.record(5.0, 0)
    assert counter.total == 0
