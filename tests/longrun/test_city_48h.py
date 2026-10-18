"""A never-ending city for two simulated days (II §17, I §6.2; DESIGN §5).

A scripted population arrives and leaves on a daily rhythm in city C (a Sentinel garrison, free
Munchers, a weather program), walking, shooting, ordering, dying, chatting and leaving by a clean
quit, a dropped line or silence. Every step is checked: nothing is logged as an error, every message
encodes, no client is sent a create for an id twice (its registry never unlinks one, I §6.2), an
empty city is reset unless a game is held for someone (S13), and what the server remembers is
bounded.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

import pytest

from fixtures.citysim import CitySim
from fixtures.loopback import SHIPPED
from opencs.config import CityConfig, load_config
from opencs.world.ids import IdAllocator, IdRange

#: Small ranges, so that two days exhaust them and reuse happens many times. The player range is
#: still wider than a busy evening's population.
SMALL = {
    IdRange.PLAYER: (0, 19),
    IdRange.MUNCHER: (260, 263),
    IdRange.SENTINEL: (4096, 4101),
    IdRange.STRUCTURE: (8192, 8231),
}


def city_c() -> CityConfig:
    return next(c for c in load_config(SHIPPED).cities if c.letter == "C")


@pytest.fixture
def recycled(monkeypatch: pytest.MonkeyPatch) -> Counter[tuple[int, bool]]:
    """How many allocations reused an id, by range's low end and whether a client was connected
    (the case the guard exists for)."""
    counts: Counter[tuple[int, bool]] = Counter()
    real = IdAllocator.allocate

    def counting(
        self: IdAllocator, earliest_start: float | None, holds: Callable[[int], bool] | None = None
    ) -> int | None:
        reusing = self._next > self._high
        got = real(self, earliest_start, holds)
        if reusing and got is not None:
            counts[(self._low, earliest_start is not None)] += 1
        return got

    monkeypatch.setattr(IdAllocator, "allocate", counting)
    return counts


def assert_bounded(sim: CitySim, ceiling: int) -> None:
    sizes = sim.size_by_hour
    assert len(sizes) >= 47
    assert max(sizes) <= ceiling, sizes
    # What an empty, reset city keeps does not grow from game to game: nothing leaks across a reset.
    assert sim.empty_sizes, "the city never stood empty"
    assert max(sim.empty_sizes) <= 3 * min(sim.empty_sizes) + 500, sim.empty_sizes


def test_two_days_with_the_real_id_ranges() -> None:
    sim = CitySim(city_c(), seed=1)
    sim.run(48)
    assert sim.joined > 150 and sim.max_players >= 6
    assert sim.refusals == {}  # no join was refused for want of an id
    assert sim.resets >= 2  # the quiet nights empty the city and it starts fresh (S13)
    assert sim.resumed >= 1  # a dropped account came back to its held game
    assert_bounded(sim, 40_000)  # the id ranges' own sizes are most of it


def test_ids_are_recycled_across_days_and_never_under_a_client_that_may_hold_them(
    recycled: Counter[tuple[int, bool]],
) -> None:
    sim = CitySim(city_c(), seed=2, id_ranges=SMALL, rate_scale=1.5)
    sim.run(48)  # a duplicate create to any client would have failed the run
    assert recycled[(0, True)] > 0, recycled  # player ids reused with clients connected
    assert recycled[(8192, True)] + recycled[(8192, False)] > 0, recycled  # towers, drops, mines
    assert sim.joined > 150
    assert_bounded(sim, 6_000)
