"""A city's territory: the block-class map and who controls which block (I §8.22, I §9.2.6, II §5).

The client computes control and income itself, from the towers it has been sent and the classes
``'T'`` gave it (I §9.2.6); the server sends no score for territory. This module is
what the server needs of it: the ``'T'``-``'I'`` message (it is ``CityState``, so
``entities.inform`` hands it to every client once, I §7.3) and a pure control computation for
the server's own use (logging; M9's Muncher goes for the team owning most blocks).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Iterable, Sequence

from opencs.messages import ServerMessage, limits, server

#: The key under which a ``ClientView`` records the grid it was sent.
TERRITORY_KEY = "territory"

type Block = tuple[int, int]  # (cell X, cell Z)


class Territory:
    """One city's block-class map; ``None`` blocks leave the client's default (all worth 1)."""

    def __init__(self, blocks: Sequence[Sequence[int]] | None) -> None:
        self._rows = blocks

    @property
    def state_key(self) -> str:
        return TERRITORY_KEY

    @property
    def state_value(self) -> Hashable:
        """The grid a client should hold: it never changes in a city's life, so it is sent once."""
        return None if self._rows is None else tuple(tuple(row) for row in self._rows)

    def class_at(self, x: int, z: int) -> int:
        """The class of block (X, Z); 0 where no map is configured (the client's default)."""
        return 0 if self._rows is None else self._rows[z][x]

    def value_at(self, x: int, z: int) -> int:
        """Points per second block (X, Z) is worth to its owner, before the multiplier."""
        return limits.BLOCK_VALUES[self.class_at(x, z)]

    def state_messages(self, now: float) -> Sequence[ServerMessage]:
        """The ``'T'``-``'I'`` grid in world order (byte ``8*X + Z`` is block (X, Z), I §8.22), or
        nothing when the city has no map."""
        if self._rows is None:
            return ()
        side = limits.BLOCK_SIDE
        return (
            server.SetBlocks(tuple(self._rows[z][x] for x in range(side) for z in range(side))),
        )

    def controllers(self, towers: Iterable[tuple[Block, int]]) -> dict[Block, int]:
        """Block -> the one team that controls it, from ``(block, team)`` for each tower. A block
        is controlled iff exactly one team has towers in it; empty and disputed blocks, and towers
        off the map, count for nobody (I §9.2.6)."""
        teams: dict[Block, set[int]] = {}
        for (x, z), team in towers:
            if 0 <= x < limits.BLOCK_SIDE and 0 <= z < limits.BLOCK_SIDE:
                teams.setdefault((x, z), set()).add(team)
        return {block: next(iter(t)) for block, t in teams.items() if len(t) == 1}

    def income(self, controlled: dict[Block, int]) -> dict[int, int]:
        """Team -> sum of its controlled blocks' values: what each client adds, times its
        multiplier, every second."""
        totals: Counter[int] = Counter()
        for (x, z), team in controlled.items():
            totals[team] += self.value_at(x, z)
        return dict(totals)
