"""Territory: the ``'T'``-``'I'`` grid in world order and the control rule (I §8.22, I §9.2.6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from opencs.config import load_config
from opencs.messages import server
from opencs.world.territory import Territory

SHIPPED = Path(__file__).parents[3] / "config" / "server.toml"

#: Rows are cell Z 0..7, digits cell X 0..7 (the shipped maps, II §5).
SHIPPED_MAPS = {
    "A": (
        "00000000 00000000 00111100 00111100 00111100 00111100 00000000 00000000",
        80,
    ),
    "B": (
        "00000000 01111110 01222210 01233210 01233210 01222210 01111110 00000000",
        168,
    ),
    "C": (
        "11111111 12222221 12333321 12333321 12333321 12333321 12222221 11111111",
        316,
    ),
}


def rows(text: str) -> tuple[tuple[int, ...], ...]:
    return tuple(tuple(int(c) for c in row) for row in text.split())


def grid(territory: Territory) -> server.SetBlocks:
    (message,) = territory.state_messages(0.0)
    assert isinstance(message, server.SetBlocks)
    return message


@pytest.mark.parametrize("letter", sorted(SHIPPED_MAPS))
def test_the_shipped_maps_are_the_specified_ones_and_stay_under_the_score_overflow(
    letter: str,
) -> None:
    text, total = SHIPPED_MAPS[letter]
    city = next(c for c in load_config(SHIPPED).cities if c.letter == letter)
    assert city.blocks == rows(text)
    values = (1, 2, 5, 10)
    assert sum(values[c] for r in city.blocks for c in r) == total
    assert total * city.max_multiplier < 32768


@pytest.mark.parametrize("letter", sorted(SHIPPED_MAPS))
def test_the_grid_message_is_in_world_order(letter: str) -> None:
    """Byte 8*X + Z is block (X, Z); the config's rows are Z, so the grid is the transpose."""
    text, _ = SHIPPED_MAPS[letter]
    r = rows(text)
    message = grid(Territory(r))
    assert len(message.classes) == 64
    for x in range(8):
        for z in range(8):
            assert message.classes[8 * x + z] == r[z][x]
            assert message.class_at(x, z) == r[z][x]


def test_an_asymmetric_map_is_not_transposed_by_mistake() -> None:
    r = [[0] * 8 for _ in range(8)]
    r[2][5] = 3  # cell X 5, cell Z 2
    message = grid(Territory(tuple(tuple(x) for x in r)))
    assert message.classes[8 * 5 + 2] == 3
    assert message.classes[8 * 2 + 5] == 0
    assert sum(message.classes) == 3


def test_no_map_sends_nothing_and_every_block_is_worth_one() -> None:
    t = Territory(None)
    assert t.state_messages(0.0) == ()
    assert t.value_at(3, 4) == 1


def test_a_block_is_controlled_by_the_only_team_with_towers_in_it() -> None:
    t = Territory(rows(SHIPPED_MAPS["B"][0]))
    held = t.controllers([((3, 3), 0), ((3, 3), 0), ((4, 4), 1), ((4, 4), 2), ((1, 1), 3)])
    assert held == {(3, 3): 0, (1, 1): 3}  # (4, 4) is disputed: nobody's


def test_off_map_towers_control_nothing_and_income_sums_values() -> None:
    t = Territory(rows(SHIPPED_MAPS["B"][0]))
    held = t.controllers([((-1, 0), 0), ((8, 3), 0), ((3, 3), 1), ((1, 1), 1), ((0, 0), 1)])
    assert held == {(3, 3): 1, (1, 1): 1, (0, 0): 1}
    assert t.income(held) == {1: 10 + 2 + 1}  # classes 3, 1, 0
