"""Players whose lines can drop: a city of account players in play, and the held slots a session
layer would report to it (II §13)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from fixtures.delivery import deliveries
from fixtures.lobbies import city
from fixtures.pods import report
from opencs.messages import ServerMessage, WirePos, client
from opencs.world.city import City
from opencs.world.player import Player
from opencs.world.resume import ResumeRecord
from opencs.world.seating import JoinResult


class Held:
    """A stand-in for the session layer's held slots."""

    def __init__(self) -> None:
        self.held: list[tuple[str, int]] = []

    def slots(self, letter: str) -> Sequence[tuple[str, int]]:
        return list(self.held)


def at(x: int, z: int = 3000) -> client.PodState:
    return replace(report(0), pos=WirePos.from_world(x, 20, z))


def game(
    teams: list[int], *, identities: bool = True, reservations: Held | None = None
) -> tuple[City, list[int]]:
    """Players of these teams, all in play at x = 2000 + 100 * i, with account identities."""
    c = City(city("A", max_module_level=2, max_multiplier=8), reservations=reservations)
    ids = []
    for i, team in enumerate(teams):
        r = c.join(f"P{i}", team, 0.0, identity=f"acct:P{i}" if identities else None)
        assert isinstance(r, JoinResult)
        ids.append(r.player_id)
    for pid in ids:
        c.client_message(pid, report(4), 0.5)
    for i, pid in enumerate(ids):
        c.client_message(pid, at(2000 + 100 * i), 1.0)
    return c, ids


def player(c: City, pid: int) -> Player:
    p = c.player(pid)
    assert p is not None
    return p


def drop(
    c: City, pid: int, now: float, **kw: object
) -> tuple[list[tuple[int, ServerMessage]], ResumeRecord | None]:
    dropped = c.line_drop(pid, now, **kw)  # type: ignore[arg-type]
    return deliveries(c, dropped.events), dropped.record
