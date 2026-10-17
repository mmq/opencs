"""Expand a city's events into (recipient, message) pairs, the way the router would."""

from __future__ import annotations

from collections.abc import Iterable

from opencs.messages import ServerMessage
from opencs.routing import Router
from opencs.world.city import City
from opencs.world.events import Event

_ROUTER = Router()


def deliveries(city: City, events: Iterable[Event]) -> list[tuple[int, ServerMessage]]:
    """Every ``(player id, message)`` the events deliver now, in order; recipients in id order."""
    return [
        (player_id, event.message)
        for event in events
        for player_id in _ROUTER.resolve(city, event.audience)
    ]


def to(deliveries_: list[tuple[int, ServerMessage]], player_id: int) -> list[ServerMessage]:
    """The messages one player receives, in order."""
    return [m for pid, m in deliveries_ if pid == player_id]
