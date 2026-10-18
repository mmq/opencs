"""Id ranges and the reuse guard (I §6.2, II §8)."""

from __future__ import annotations

from opencs import policy
from opencs.world.ids import IdAllocator, IdRange, IdSpace


def exhausted(low: int = 5, high: int = 7) -> IdAllocator:
    a = IdAllocator(low, high)
    assert [a.allocate(None) for _ in range(high - low + 1)] == list(range(low, high + 1))
    return a


def test_ids_go_out_in_order_from_the_low_end() -> None:
    a = IdAllocator(10, 12)
    assert [a.allocate(0.0) for _ in range(3)] == [10, 11, 12]


def test_a_released_id_is_not_reused_while_fresh_ones_remain() -> None:
    a = IdAllocator(0, 3)
    first = a.allocate(None)
    assert first == 0
    a.release(0, now=1.0)
    assert a.allocate(None) == 1  # in order, not the released one


def test_exhaustion_with_nothing_released_returns_none() -> None:
    assert exhausted().allocate(None) is None


def test_the_last_id_of_a_range_is_allocated_and_the_next_is_refused() -> None:
    a = IdAllocator(0, 1)
    assert [a.allocate(None), a.allocate(None), a.allocate(None)] == [0, 1, None]


def test_reuse_when_nobody_is_connected() -> None:
    a = exhausted()
    a.release(6, now=10.0)
    assert a.allocate(None) == 6


def test_reuse_only_if_every_client_started_after_the_release() -> None:
    a = exhausted()
    a.release(6, now=10.0)
    assert a.allocate(earliest_start=9.0) is None  # started before: may still hold it
    assert a.allocate(earliest_start=10.0) is None  # same instant: not provably safe
    assert a.allocate(earliest_start=10.5) == 6  # started after the release


def test_the_earliest_released_id_goes_first_and_blocks_later_ones() -> None:
    a = exhausted()
    a.release(7, now=1.0)
    a.release(5, now=2.0)
    # 5 (released at 2.0) would be safe for a client that started at 2.5, but 7 is first in line
    # and is not safe for one that started at 0.5: nothing is handed out.
    assert a.allocate(earliest_start=0.5) is None
    assert a.allocate(earliest_start=1.5) == 7
    assert a.allocate(earliest_start=1.5) is None  # 5 was released at 2.0, after this client
    assert a.allocate(earliest_start=2.5) == 5


def test_a_reused_id_is_gone_from_the_released_queue() -> None:
    a = exhausted()
    a.release(6, now=1.0)
    assert a.allocate(None) == 6
    assert a.allocate(None) is None


def test_ranges_are_independent() -> None:
    space = IdSpace()
    assert space.allocate(IdRange.PLAYER, None) == 0
    assert space.allocate(IdRange.PLAYER, None) == 1
    assert space.allocate(IdRange.GUPPY, None) == 250
    assert space.allocate(IdRange.STRUCTURE, None) == 8192
    space.release(IdRange.PLAYER, 0, now=1.0)
    assert space.allocate(IdRange.GUPPY, None) == 251


def test_the_player_range_is_the_whole_pod_byte_space() -> None:
    space = IdSpace()
    got = [space.allocate(IdRange.PLAYER, None) for _ in range(241)]
    assert got[:240] == list(range(240))  # a pod id is one bias10 byte (II §8)
    assert got[240] is None


def test_id_ranges() -> None:
    assert IdRange.PLAYER.value == (0, 239)  # ours: rival pods 32-239 are not built
    assert IdRange.GUPPY.value == (250, 259)
    assert IdRange.MUNCHER.value == (260, 4095)
    assert IdRange.SENTINEL.value == (4096, 8191)
    assert IdRange.STRUCTURE.value == (8192, 32767)
    assert policy.PLAYER_IDS[1] == 239  # the last id a bias10 byte can carry


def test_an_id_released_twice_is_handed_out_once() -> None:
    a = exhausted()
    a.release(5, now=1.0)
    a.release(5, now=2.0)
    assert [a.allocate(None), a.allocate(None)] == [5, None]


def test_an_id_that_was_never_handed_out_cannot_be_released_into_the_pool() -> None:
    a = IdAllocator(5, 9)
    a.allocate(None)  # 5
    a.release(8, now=1.0)  # still fresh
    a.release(99, now=1.0)  # outside the range
    a.release(4, now=1.0)
    assert [a.allocate(None) for _ in range(5)] == [6, 7, 8, 9, None]


def test_a_reused_id_can_be_released_and_reused_again() -> None:
    a = exhausted()
    a.release(6, now=1.0)
    assert a.allocate(None) == 6
    a.release(6, now=2.0)
    assert a.allocate(None) == 6


# --- the exact guard: what a connected client has been sent -------------------------------------


def test_an_id_no_connected_client_has_seen_is_reused_though_a_client_started_before_it() -> None:
    a = exhausted()
    a.release(6, now=10.0)
    assert a.allocate(earliest_start=1.0) is None  # the join-time rule alone refuses
    assert a.allocate(earliest_start=1.0, holds=lambda i: False) == 6


def test_an_id_a_connected_client_has_seen_is_not_reused() -> None:
    a = exhausted()
    a.release(6, now=10.0)
    assert a.allocate(earliest_start=1.0, holds=lambda i: i == 6) is None
    assert a.allocate(earliest_start=1.0, holds=lambda i: False) == 6  # once it has gone


def test_the_first_safe_released_id_is_taken_not_just_the_first_released() -> None:
    a = exhausted()
    a.release(5, now=10.0)
    a.release(6, now=11.0)
    assert a.allocate(earliest_start=1.0, holds=lambda i: i == 5) == 6
    assert a.allocate(earliest_start=1.0, holds=lambda i: i == 5) is None  # 5 is still held
    assert a.allocate(earliest_start=1.0, holds=lambda i: False) == 5


def test_only_the_oldest_released_ids_are_looked_through(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(policy, "ID_REUSE_SCAN", 2)
    a = exhausted(0, 3)
    for n in range(4):
        a.release(n, now=10.0 + n)
    assert a.allocate(earliest_start=1.0, holds=lambda i: i in (0, 1)) is None
    assert a.allocate(earliest_start=1.0, holds=lambda i: i in (0, 1, 2)) is None
