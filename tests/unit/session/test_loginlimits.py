"""Password checks and new accounts are metered across connections (DESIGN §5)."""

from __future__ import annotations

from fixtures.sessions import World, connect, make_world
from opencs import policy
from opencs.clock import ManualClock
from opencs.lobby.identity import Pilot
from opencs.session.loginlimits import LoginLimits
from opencs.session.ratelimit import KeyedBuckets


def attempt(world: World, line: str, address: str) -> bytes:
    """A fresh connection from ``address`` sends one account line; what the server answered."""
    h = connect(world, address=address)
    h.feed(f"HHH\r{line}\r")
    out = h.take()
    h.session.closed()  # the player hangs up: the account is free again
    return out


def register(world: World, *names: str) -> None:
    """Accounts that exist already, made without the gate."""
    for name in names:
        pilot = world.identity.login(f"{name},pw")
        assert isinstance(pilot, Pilot)
        world.identity.release(pilot)


def refused(out: bytes, fragment: bytes) -> bool:
    return fragment in out


def test_one_address_is_limited_in_password_checks_across_connections() -> None:
    world = make_world()
    register(world, "ACCT")
    burst = policy.LOGIN_ATTEMPTS_PER_ADDRESS[0]
    outs = [attempt(world, f"ACCT,wrong{n}", "1.1.1.1") for n in range(burst + 1)]
    assert not any(refused(o, b"TOO MANY") for o in outs[:burst])
    assert refused(outs[burst], b"TOO MANY LOGIN ATTEMPTS")


def test_the_allowance_comes_back_with_time() -> None:
    world = make_world()
    register(world, "ACCT")
    burst, rate = policy.LOGIN_ATTEMPTS_PER_ADDRESS
    for n in range(burst):
        attempt(world, f"ACCT,wrong{n}", "1.1.1.1")
    assert refused(attempt(world, "ACCT,x", "1.1.1.1"), b"TOO MANY")
    world.clock.advance(1 / rate - 1)
    assert refused(attempt(world, "ACCT,x", "1.1.1.1"), b"TOO MANY")
    world.clock.advance(1)
    assert not refused(attempt(world, "ACCT,x", "1.1.1.1"), b"TOO MANY")


def test_another_address_is_not_affected() -> None:
    world = make_world()
    register(world, "ACCT", "OTHER")
    for n in range(policy.LOGIN_ATTEMPTS_PER_ADDRESS[0] + 1):
        attempt(world, f"ACCT,wrong{n}", "1.1.1.1")
    assert not refused(attempt(world, "OTHER,pw", "2.2.2.2"), b"TOO MANY")


def test_guests_cost_nothing() -> None:
    world = make_world()
    for _ in range(policy.LOGIN_ATTEMPTS_PER_ADDRESS[0] * 3):
        assert refused(attempt(world, "JOINGENIE", "1.1.1.1"), b"PASSWORD: ")


def test_new_accounts_are_limited_per_address() -> None:
    world = make_world()
    burst = policy.NEW_ACCOUNTS_PER_ADDRESS[0]
    outs = [attempt(world, f"NEW{n},pw", "1.1.1.1") for n in range(burst + 1)]
    assert not any(refused(o, b"TOO MANY") for o in outs[:burst])
    assert refused(outs[burst], b"TOO MANY NEW ACCOUNTS")
    # An existing account still logs in from that address: only registration is refused.
    assert refused(attempt(world, "NEW0,pw", "1.1.1.1"), b"PASSWORD: ")


def test_new_accounts_are_limited_for_the_whole_server() -> None:
    world = make_world()
    burst = policy.NEW_ACCOUNTS_GLOBAL[0]
    outs = [attempt(world, f"NEW{n},pw", f"10.0.0.{n}") for n in range(burst + 1)]
    assert not any(refused(o, b"TOO MANY") for o in outs[:burst])
    assert refused(outs[burst], b"TOO MANY NEW ACCOUNTS")


def test_password_checks_are_limited_for_the_whole_server() -> None:
    world = make_world()
    burst = policy.LOGIN_ATTEMPTS_GLOBAL[0]
    register(world, *(f"ACCT{n}" for n in range(burst + 1)))
    outs = [attempt(world, f"ACCT{n},wrong", f"10.0.{n}.1") for n in range(burst + 1)]
    assert not any(refused(o, b"TOO MANY") for o in outs[:burst])
    assert refused(outs[burst], b"TOO MANY LOGIN ATTEMPTS")


def test_a_refused_address_does_not_drain_the_global_allowance() -> None:
    clock = ManualClock(0.0)
    limits = LoginLimits(clock)
    for _ in range(policy.LOGIN_ATTEMPTS_PER_ADDRESS[0]):
        assert limits.attempt("1.1.1.1")
    for _ in range(100):
        assert not limits.attempt("1.1.1.1")
    # The global bucket was charged only for the allowed ones.
    spent = policy.LOGIN_ATTEMPTS_PER_ADDRESS[0]
    for n in range(policy.LOGIN_ATTEMPTS_GLOBAL[0] - spent):
        assert limits.attempt(f"9.9.9.{n}")


def test_the_table_of_sources_is_bounded() -> None:
    buckets = KeyedBuckets(1, 1.0)
    for n in range(policy.MAX_TRACKED_SOURCES * 2):
        buckets.take(f"a{n}", 0.0)  # each spends its only token: none is full, none can be pruned
    assert len(buckets) <= policy.MAX_TRACKED_SOURCES


def test_refilled_sources_are_forgotten_first() -> None:
    buckets = KeyedBuckets(1, 1.0)
    for n in range(policy.MAX_TRACKED_SOURCES):
        buckets.take(f"a{n}", 0.0)
    buckets.take("fresh", 10.0)  # the table was full, every old bucket had refilled
    assert len(buckets) == 1
