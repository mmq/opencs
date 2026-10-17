"""Telling a client an entity exists, exactly once (I §7.2, I §7.3).

The client's registry has no duplicate check for a pod's ``'G'`` and never unlinks, so each client
must be told about each entity once. ``introduce`` is that one path: it is what a spawn uses for
every client already here and what a late joiner's sync uses for every entity already live, so the
two can never disagree. A pod is introduced with ``'G'`` + ``'A'``; any other kind (tower, drop,
mine, Sentinel, guppy, Muncher) joins by implementing the same two members.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from typing import Protocol, runtime_checkable

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


@runtime_checkable
class TracksClients(Protocol):
    """An entity that keeps something per client beyond whether it holds it (a guppy's leg count,
    I §7.3): told when a client is introduced, in the same call that sends the create."""

    def client_introduced(self, view: ClientView) -> None: ...


def introduce(entity: Entity, view: ClientView, now: float) -> list[Event]:
    """The packets that make ``view``'s client hold ``entity``, and the note that it now does.
    Empty if it already holds it: never a second create."""
    if view.holds(entity.entity_id):
        return []
    view.hold(entity.entity_id)
    if isinstance(entity, TracksClients):
        entity.client_introduced(view)
    return [Event(m, ToPlayer(view.player_id)) for m in entity.creating_messages(now)]


class CityState(Protocol):
    """City-wide state a client is sent that is not an entity: the territory grid, the weather.
    ``state_value`` is what a client's copy should equal now; a client is sent
    ``state_messages`` whenever its copy differs, so the grid goes once and the weather goes again
    on every change (I §7.3)."""

    @property
    def state_key(self) -> str: ...

    @property
    def state_value(self) -> Hashable: ...

    def state_messages(self, now: float) -> Sequence[ServerMessage]: ...


@runtime_checkable
class CatchesUp(Protocol):
    """City-wide state a client may hold an *older value* of, which is brought up to date by what
    differs (the weather: a rain level back to 0 must be sent, one that did not change must not be
    sent again) instead of by the whole state."""

    def messages_since(self, told: Hashable, now: float) -> Sequence[ServerMessage]: ...


def inform(state: CityState, view: ClientView, now: float) -> list[Event]:
    """The packets that bring ``view``'s client up to date with ``state``, or none if it is.
    The sibling of ``introduce`` for state that is not an entity; a joiner's sync calls both."""
    if view.was_told(state.state_key, state.state_value):
        return []
    told = view.told.get(state.state_key)
    view.tell(state.state_key, state.state_value)
    if isinstance(state, CatchesUp) and told is not None:
        return [Event(m, ToPlayer(view.player_id)) for m in state.messages_since(told, now)]
    return [Event(m, ToPlayer(view.player_id)) for m in state.state_messages(now)]
