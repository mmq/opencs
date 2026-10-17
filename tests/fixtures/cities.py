"""Seated cities for the combat and scoring tests."""

from __future__ import annotations

from collections.abc import Sequence

from fixtures.lobbies import city
from fixtures.pods import report
from opencs.messages import ServerMessage
from opencs.world.city import City, JoinResult


def seat(
    teams: Sequence[int], *, reported: bool = True, now: float = 0.5, **config: object
) -> tuple[City, list[int]]:
    """A city with one player per entry of ``teams`` (ids 0, 1, ...). With ``reported``, each has
    sent its first ``'b'`` (state 4, the drop) at ``now``, so all are introduced to each other."""
    c = City(city("A", **config))
    ids = []
    for i, team in enumerate(teams):
        r = c.join(f"P{i}", team, 0.0)
        assert isinstance(r, JoinResult)
        ids.append(r.player_id)
    if reported:
        for pid in ids:
            c.client_message(pid, report(4), now)
    return c, ids


class Npc:
    """A stand-in for any spawned non-pod entity (a Sentinel, a tower...)."""

    def __init__(self, entity_id: int) -> None:
        self.entity_id = entity_id

    def creating_messages(self, now: float) -> Sequence[ServerMessage]:
        return ()
