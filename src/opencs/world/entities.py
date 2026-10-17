"""Telling a client an entity exists, exactly once (I §7.2, I §7.3).

The client's registry has no duplicate check for a pod's ``'G'`` and never unlinks, so each client
must be told about each entity once. ``introduce`` is that one path: it is what a spawn uses for
every client already here and what a late joiner's sync uses for every entity already live, so the
two can never disagree. A pod is introduced with ``'G'`` + ``'A'``; any other kind (tower, drop,
mine, Sentinel, guppy, Muncher) joins by implementing the same two members.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from opencs.messages import ServerMessage
from opencs.world.events import Event, ToPlayer
from opencs.world.view import ClientView


class Entity(Protocol):
    """Anything a client must be told about: an id and the packets that create it."""

    @property
    def entity_id(self) -> int: ...

    def creating_messages(self, now: float) -> Sequence[ServerMessage]:
        """What a client needs to receive, in order, to hold this entity as it stands at ``now``."""
        ...


def introduce(entity: Entity, view: ClientView, now: float) -> list[Event]:
    """The packets that make ``view``'s client hold ``entity``, and the note that it now does.
    Empty if it already holds it: never a second create."""
    if view.holds(entity.entity_id):
        return []
    view.hold(entity.entity_id)
    return [Event(m, ToPlayer(view.player_id)) for m in entity.creating_messages(now)]
