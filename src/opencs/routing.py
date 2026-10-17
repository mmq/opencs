"""Audience to connections (I App. B, DESIGN §3.2, ADR 4).

A city says who should hear a message as an ``Audience``; the router knows which in-game session
speaks for each player and turns the one into the other. It knows nothing of rules: it reads the
city's roster (id, team, what each client holds) and nothing else.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Protocol

from opencs.messages import ServerMessage
from opencs.world.city import City
from opencs.world.events import (
    Audience,
    Event,
    ToAll,
    ToAllExcept,
    ToHolders,
    ToPlayer,
    ToTeam,
)

log = logging.getLogger("opencs.routing")


class Sink(Protocol):
    """Where a player's messages go: its in-game session."""

    def send(self, msg: ServerMessage) -> None: ...


class Router:
    """The directory of ``(city letter, player id) -> sink`` and the audience resolver."""

    def __init__(self) -> None:
        self._sinks: dict[tuple[str, int], Sink] = {}

    def register(self, letter: str, player_id: int, sink: Sink) -> None:
        self._sinks[(letter, player_id)] = sink

    def unregister(self, letter: str, player_id: int) -> None:
        """Stop delivering to this player (it quit, or its line dropped)."""
        self._sinks.pop((letter, player_id), None)

    def resolve(self, city: City, audience: Audience) -> list[int]:
        """The ids of the players in ``city`` that ``audience`` names, in id order."""
        players = city.players()
        match audience:
            case ToPlayer(player_id):
                return [p.id for p in players if p.id == player_id]
            case ToAll():
                return [p.id for p in players]
            case ToAllExcept(player_id):
                return [p.id for p in players if p.id != player_id]
            case ToTeam(team):
                return [p.id for p in players if p.team == team]
            case ToHolders(entity_id):
                return [p.id for p in players if p.view.holds(entity_id)]

    def deliver(self, city: City, events: Iterable[Event]) -> None:
        """Send each event to the sinks of its audience. A player with no sink (not yet seated in
        a session, or gone) is skipped; one sink failing never stops the others."""
        letter = city.config.letter
        for event in events:
            for player_id in self.resolve(city, event.audience):
                sink = self._sinks.get((letter, player_id))
                if sink is None:
                    continue
                try:
                    sink.send(event.message)
                except Exception:
                    log.exception("delivery to player %d of city %s failed", player_id, letter)
