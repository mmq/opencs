from opencs.store.base import Account
from opencs.store.memory import MemoryAccountStore


def test_create_get_and_duplicate() -> None:
    store = MemoryAccountStore()
    account = Account("ALICE", b"salt", b"hash", 1)
    assert store.get("ALICE") is None
    assert store.create(account)
    assert store.get("ALICE") == account
    assert not store.create(Account("ALICE", b"other", b"other", 1))
    assert store.get("ALICE") == account


def test_callsign_registered_compares_the_cut_names() -> None:
    store = MemoryAccountStore()
    store.create(Account("ALPHAXYZ", b"s", b"h", 1))
    assert store.callsign_registered("ALPHAXYZ", 8)
    assert not store.callsign_registered("ALPHAXY", 8)
    assert store.callsign_registered("ALPHA", 5)
