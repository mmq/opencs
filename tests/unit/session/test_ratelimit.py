"""TokenBucket: bursts, refill, no overdraft."""

from opencs.session.ratelimit import TokenBucket


def test_a_full_bucket_allows_its_capacity_then_refuses() -> None:
    bucket = TokenBucket(3, 1.0, 0.0)
    assert [bucket.take(0.0) for _ in range(4)] == [True, True, True, False]


def test_it_refills_at_the_rate_up_to_capacity() -> None:
    bucket = TokenBucket(2, 0.5, 0.0)
    assert bucket.take(0.0) and bucket.take(0.0) and not bucket.take(0.0)
    assert not bucket.take(1.0)
    assert bucket.take(2.0)
    assert [bucket.take(1000.0) for _ in range(3)] == [True, True, False]
