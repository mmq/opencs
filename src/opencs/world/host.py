"""What the world's rule modules need of the city that owns them (DESIGN §3.1).

``Orders``, ``Drops`` and ``Effects`` each hold one city's state but cannot create an entity, reach
the roster or hand out an id themselves: only the city can. They ask through this protocol, so none
of them imports the city.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from opencs.world.entities import Entity
from opencs.world.events import Event, ToPlayers
from opencs.world.ids import IdRange
from opencs.world.player import Player


class WorldHost(Protocol):
    """The city, as its rule modules see it."""

    def players(self) -> Sequence[Player]: ...

    def player(self, player_id: int) -> Player | None: ...

    def playing(self, *, team: int | None = None, but: int | None = None) -> ToPlayers:
        """The clients that have reported (see ``City.playing``)."""
        ...

    def is_known_npc(self, entity_id: int) -> bool: ...

    def spawn(self, entity: Entity, now: float) -> list[Event]: ...

    def despawn(self, entity_id: int) -> None: ...

    def allocate_structure_id(self) -> int | None:
        """A free id in the structure range, or None if none is safely reusable (II §8)."""
        ...

    def release_structure_id(self, entity_id: int, now: float) -> None: ...

    def allocate_id(self, which: IdRange) -> int | None:
        """A free id in ``which``, or None if none is safely reusable (I §6.2)."""
        ...

    def release_id(self, which: IdRange, entity_id: int, now: float) -> None: ...
