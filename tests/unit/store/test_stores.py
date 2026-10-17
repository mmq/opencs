"""The store protocols, checked against every implementation (DESIGN §3, ADR 7)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from opencs.store.base import Account, ProgressRecord, Stores
from opencs.store.memory import MemoryAccountStore, MemoryProgressStore

STAMP = 1_700_000_000.5

type Factory = Callable[[Path], Stores]


def _memory(tmp_path: Path) -> Stores:
    return Stores(MemoryAccountStore(), MemoryProgressStore(lambda: STAMP))


FACTORIES: dict[str, Factory] = {"memory": _memory}


@pytest.fixture(params=sorted(FACTORIES))
def stores(request: pytest.FixtureRequest, tmp_path: Path) -> Stores:
    return FACTORIES[request.param](tmp_path)


RECORD = ProgressRecord(
    bays=(3, 2, 1, 1, 5, 4, 0, 1, 2), marker=-7, bay_points=957, score=1670, career=90_000
)


def test_an_account_is_created_once_and_read_back(stores: Stores) -> None:
    account = Account("ALICE", b"\x00salt\xff", b"\x01hash\xfe", 1234)
    assert stores.accounts.get("ALICE") is None
    assert stores.accounts.create(account)
    assert stores.accounts.get("ALICE") == account
    assert not stores.accounts.create(Account("ALICE", b"other", b"other", 1))
    assert stores.accounts.get("ALICE") == account


def test_callsign_registered_compares_the_cut_names(stores: Stores) -> None:
    stores.accounts.create(Account("ALPHAXYZ", b"s", b"h", 1))
    assert stores.accounts.callsign_registered("ALPHAXYZ", 8)
    assert not stores.accounts.callsign_registered("ALPHAXY", 8)
    assert stores.accounts.callsign_registered("ALPHA", 5)
    assert not stores.accounts.callsign_registered("BRAVO", 5)


def test_progress_round_trips_every_field_and_is_stamped(stores: Stores) -> None:
    assert stores.progress.load("acct:ALICE") is None
    stores.progress.save("acct:ALICE", RECORD)
    loaded = stores.progress.load("acct:ALICE")
    assert loaded is not None
    assert loaded.updated == STAMP
    assert loaded == replace(RECORD, updated=STAMP)


def test_a_save_replaces_the_whole_record_and_other_identities_are_untouched(
    stores: Stores,
) -> None:
    stores.progress.save("acct:ALICE", RECORD)
    stores.progress.save("JOINGENI", ProgressRecord((1,) * 9, 0, 5, 6, 7))
    newer = ProgressRecord((5,) * 9, 119, 0, 3, 4)
    stores.progress.save("acct:ALICE", newer)
    alice, guest = stores.progress.load("acct:ALICE"), stores.progress.load("JOINGENI")
    assert alice is not None and guest is not None
    assert (alice.bays, alice.marker, alice.score, alice.career) == ((5,) * 9, 119, 3, 4)
    assert (guest.bays, guest.bay_points, guest.score, guest.career) == ((1,) * 9, 5, 6, 7)


def test_identity_keys_are_case_sensitive_and_exact(stores: Stores) -> None:
    stores.progress.save("acct:ALICE", RECORD)
    assert stores.progress.load("acct:alice") is None
    assert stores.progress.load("ALICE") is None


def test_negative_markers_and_large_scores_survive(stores: Stores) -> None:
    big = ProgressRecord((0,) * 9, -136, 9_999_999, 10**13, 10**15)
    stores.progress.save("k", big)
    loaded = stores.progress.load("k")
    assert loaded is not None
    assert (loaded.marker, loaded.bay_points, loaded.score, loaded.career) == (
        -136,
        9_999_999,
        10**13,
        10**15,
    )
