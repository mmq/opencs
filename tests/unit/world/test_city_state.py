"""City-wide state that is not an entity is sent once, and again on every change (I §7.3)."""

from __future__ import annotations

from collections.abc import Hashable, Sequence

from opencs.messages import ServerMessage, server
from opencs.world.entities import inform
from opencs.world.events import ToPlayer
from opencs.world.view import ClientView


class Weather:
    """Stands in for M9's weather: a state whose value changes."""

    state_key = "weather"

    def __init__(self, kind: int) -> None:
        self.kind = kind

    @property
    def state_value(self) -> Hashable:
        return self.kind

    def state_messages(self, now: float) -> Sequence[ServerMessage]:
        return (server.OrderReply(self.kind),)


def test_state_is_sent_once_then_again_only_when_it_changes() -> None:
    view = ClientView(7, joined_at=0.0)
    w = Weather(1)
    first = inform(w, view, 0.0)
    assert [(e.message, e.audience) for e in first] == [(server.OrderReply(1), ToPlayer(7))]
    assert inform(w, view, 1.0) == []
    w.kind = 2
    assert [e.message for e in inform(w, view, 2.0)] == [server.OrderReply(2)]
    assert inform(w, view, 3.0) == []


def test_a_late_joiner_is_sent_the_current_value() -> None:
    w = Weather(3)
    early, late = ClientView(1, joined_at=0.0), ClientView(2, joined_at=50.0)
    inform(w, early, 0.0)
    assert [e.message for e in inform(w, late, 50.0)] == [server.OrderReply(3)]
    assert early.held == late.held == set()
