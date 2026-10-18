"""Seating: who may join a city (I §6.2, I §7.2)."""

from __future__ import annotations

import logging

import pytest

from fixtures.lobbies import city
from opencs.world.city import City
from opencs.world.ids import IdRange
from opencs.world.seating import JoinRefused, JoinResult


def test_a_join_refused_for_want_of_a_player_id_is_logged_with_who_holds_the_range(
    caplog: pytest.LogCaptureFixture,
) -> None:
    c = City(city("A"), id_ranges={IdRange.PLAYER: (0, 1)})
    assert isinstance(c.join("OLD", 0, 0.0), JoinResult)
    assert isinstance(c.join("NEWER", 1, 5.0), JoinResult)
    with caplog.at_level(logging.WARNING, logger="opencs.world"):
        refused = c.join("LATE", 2, 9.0)
    assert refused == JoinRefused("NO FREE PLAYER ID")
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(messages) == 1
    assert "LATE" in messages[0] and "no free player id" in messages[0]
    assert messages[0].index("OLD") < messages[0].index("NEWER")  # longest-connected first
