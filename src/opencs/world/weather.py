"""A city's weather condition (I §8.20, I §9.3.5, I §7.3).

The condition is the palette index 0-4 of ``'R'`` sub 0. It is city-wide state, not an entity, so it
is a ``CityState``: a client is sent it on its first report and again whenever it has changed
(``entities.inform``). M9 adds the city's weather programs beside ``cycle``.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence

from opencs import policy
from opencs.messages import ServerMessage, limits, server

#: The key under which a ``ClientView`` records the palette it was sent.
WEATHER_KEY = "weather"


class Weather:
    """One city's current palette."""

    def __init__(self) -> None:
        self.palette = policy.DEFAULT_PALETTE

    def cycle(self) -> int:
        """The Weather module: the next condition, 0 -> 1 -> ... -> 4 -> 0 (I §9.3.5)."""
        self.palette = (self.palette + 1) % (limits.AMBIENT_PALETTE_MAX + 1)
        return self.palette

    @property
    def state_key(self) -> str:
        return WEATHER_KEY

    @property
    def state_value(self) -> Hashable:
        return self.palette

    def state_messages(self, now: float) -> Sequence[ServerMessage]:
        """``'R'`` sub 0: the palette now, not a crossfade (I §8.20, I §9.3.5)."""
        return (server.Ambient(0, self.palette),)
