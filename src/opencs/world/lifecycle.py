"""When a city's game begins and when it ends (II §17, II §5).

A city's game never ends while anyone is in it: it begins with the first player and is reset when
nobody is left and no ``/RESUME`` slot is held for a dropped player (II §13 item 4). These are
the programs the city starts and clears at those two moments; the city keeps the flag and the id
space.
"""

from __future__ import annotations

from collections.abc import Collection

from opencs.world.effects import Effects
from opencs.world.modules import Drops
from opencs.world.npcs.munchers import Munchers
from opencs.world.npcs.sentinels import Sentinels
from opencs.world.orders import Orders
from opencs.world.weather import Weather


class Lifecycle:
    """The programs of one city that start with its game and are cleared when it is reset."""

    def __init__(
        self,
        weather: Weather,
        sentinels: Sentinels,
        munchers: Munchers,
        orders: Orders,
        drops: Drops,
        effects: Effects,
    ) -> None:
        self._weather = weather
        self._sentinels = sentinels
        self._munchers = munchers
        self._orders = orders
        self._drops = drops
        self._effects = effects

    @staticmethod
    def abandoned(players: Collection[object], held_slots: Collection[object]) -> bool:
        """Nobody is here and nothing is held for anyone: the game is over (S13)."""
        return not players and not held_slots

    def start(self, now: float) -> None:
        """The first player is in: the clocks of the city's programs begin."""
        self._weather.start(now)
        self._sentinels.start(now)  # nobody has reported yet, so nobody is sent anything
        self._munchers.start(now)

    def reset(self) -> None:
        """The next game starts fresh: every program forgets the last one."""
        self._orders.reset()
        self._drops.reset()
        self._weather.reset()
        self._sentinels.reset()
        self._munchers.reset()
        self._effects.reset()
