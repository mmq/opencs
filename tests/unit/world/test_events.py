"""Events and audiences are immutable data (DESIGN §3.2)."""

from __future__ import annotations

import dataclasses

import pytest

from opencs.messages import server
from opencs.world.events import Event, ToAll, ToAllExcept, ToHolders, ToPlayer, ToTeam


def test_an_event_pairs_a_message_with_an_audience() -> None:
    event = Event(server.ReleaseDrop(3), ToPlayer(3))
    assert event.message == server.ReleaseDrop(3)
    assert event.audience == ToPlayer(3)


def test_audiences_compare_by_value_and_differ_by_kind() -> None:
    assert ToAllExcept(1) == ToAllExcept(1)
    assert ToAllExcept(1) != ToPlayer(1)
    assert ToTeam(2) != ToHolders(2)
    assert ToAll() == ToAll()


def test_events_are_frozen() -> None:
    event = Event(server.QuitAck(), ToAll())
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.audience = ToPlayer(0)  # type: ignore[misc]
