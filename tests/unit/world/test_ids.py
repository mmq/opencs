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


def test_the_player_range_holds_32_ids() -> None:
    space = IdSpace()
    got = [space.allocate(IdRange.PLAYER, None) for _ in range(33)]
    assert got[:32] == list(range(32))
    assert got[32] is None


def test_id_ranges() -> None:
    assert IdRange.PLAYER.value == (0, 31)
    assert IdRange.RIVAL_POD.value == (32, 239)
    assert IdRange.GUPPY.value == (250, 259)
    assert IdRange.MUNCHER.value == (260, 4095)
    assert IdRange.SENTINEL.value == (4096, 8191)
    assert IdRange.STRUCTURE.value == (8192, 32767)
    assert policy.RIVAL_POD_IDS[1] == 239  # the last id a bias10 byte can carry
