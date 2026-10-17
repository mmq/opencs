"""The city feeds each pod's ledger from its weight reports and its death (I §9.3.4, II §13)."""

from __future__ import annotations

import logging

import pytest

from fixtures.cities import seat
from fixtures.pods import report
from opencs import policy
from opencs.messages import client

ROCKET_PACK = 5  # weight 4


def test_a_weight_report_confirms_a_pending_install() -> None:
    c, (a,) = seat([0])
    player = c.player(a)
    assert player is not None
    player.loadout.install(ROCKET_PACK)
    c.client_message(a, report(0, weight=4), 2.0)
    assert player.loadout.modules == [ROCKET_PACK] and player.loadout.pending == []


def test_a_lasting_disagreement_is_logged_once_and_never_acted_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    c, (a,) = seat([0])
    player = c.player(a)
    assert player is not None
    player.loadout.install(ROCKET_PACK)
    c.client_message(a, report(0, weight=4), 2.0)
    with caplog.at_level(logging.DEBUG, logger="opencs.world"):
        for i in range(policy.LOADOUT_GRACE_REPORTS + 3):
            c.client_message(a, report(0, weight=9), 3.0 + i)
    marked = [r for r in caplog.records if "LOADOUT MISMATCH" in r.getMessage()]
    assert len(marked) == 3
    assert [r.levelno for r in marked] == [logging.WARNING, logging.DEBUG, logging.DEBUG]
    assert player.loadout.modules == [ROCKET_PACK]  # untouched


def test_the_clients_death_clears_the_ledger() -> None:
    c, (a,) = seat([0])
    player = c.player(a)
    assert player is not None
    player.loadout.install(ROCKET_PACK)
    c.client_message(a, report(0, weight=4), 2.0)
    c.client_message(a, client.Died(), 3.0)
    assert player.loadout.carried == []


def test_a_second_death_report_before_the_respawn_leaves_a_new_install_alone() -> None:
    c, (a,) = seat([0])
    player = c.player(a)
    assert player is not None
    c.client_message(a, report(0), 2.0)
    c.client_message(a, client.Died(), 3.0)
    player.loadout.install(ROCKET_PACK)  # granted after the death
    c.client_message(a, client.Died(), 4.0)  # a duplicate 'J': not a new death
    assert player.loadout.carried == [ROCKET_PACK]
