"""Identity: real accounts, JOINGENIE guests, callsigns (II §3, II §3)."""

from __future__ import annotations

import pytest

from opencs.lobby.identity import Identity, Pilot, Refused, hash_password
from opencs.store.memory import MemoryAccountStore

FAST = 1000  # PBKDF2 rounds: the algorithm is the point, not the cost


def make() -> tuple[Identity, MemoryAccountStore]:
    store = MemoryAccountStore()
    return Identity(store, iterations=FAST), store


def pilot(result: Pilot | Refused) -> Pilot:
    assert isinstance(result, Pilot), result
    return result


def test_joingenie_callsigns_are_cut_to_eight_then_numbered() -> None:
    identity, _ = make()
    names = [pilot(identity.login("JOINGENIE")).callsign for _ in range(3)]
    assert names == ["JOINGENI", "JOINGEN2", "JOINGEN3"]


def test_joingenie_is_case_insensitive_and_has_no_password() -> None:
    identity, _ = make()
    first = pilot(identity.login("joingenie"))
    assert first == Pilot("JOINGENI", None)
    assert first.is_guest
    # A password after JOINGENIE is not a registration of the guest name.
    assert pilot(identity.login("JoinGenie,secret")).callsign == "JOINGEN2"


def test_a_freed_callsign_is_reused() -> None:
    identity, _ = make()
    a = pilot(identity.login("JOINGENIE"))
    b = pilot(identity.login("JOINGENIE"))
    identity.release(a)
    assert pilot(identity.login("JOINGENIE")).callsign == "JOINGENI"
    assert identity.callsign_online(b.callsign)


def test_ninth_and_later_duplicates_get_two_digit_suffixes_until_exhausted() -> None:
    identity, _ = make()
    calls = [pilot(identity.login("JOINGENIE")).callsign for _ in range(9)]
    assert calls[-1] == "JOINGEN9"
    assert pilot(identity.login("JOINGENIE")).callsign == "JOINGE10"
    for _ in range(11, 100):
        pilot(identity.login("JOINGENIE"))
    last = identity.login("JOINGENIE")
    assert isinstance(last, Refused)


def test_short_names_get_the_digit_appended() -> None:
    identity, _ = make()
    assert pilot(identity.login("BOB")).callsign == "BOB"
    assert pilot(identity.login("bob")).callsign == "BOB2"


def test_first_login_registers_the_password_and_later_ones_must_match() -> None:
    identity, store = make()
    first = pilot(identity.login("alice,hunter2"))
    assert first == Pilot("ALICE", "ALICE")
    assert not first.is_guest
    assert store.get("ALICE") is not None
    identity.release(first)
    assert isinstance(identity.login("ALICE,wrong"), Refused)
    assert isinstance(identity.login("ALICE,hunter"), Refused)
    again = identity.login("Alice,hunter2")
    assert pilot(again).callsign == "ALICE"


def test_a_second_simultaneous_login_is_refused_and_a_release_allows_it() -> None:
    identity, _ = make()
    first = pilot(identity.login("ALICE,pw"))
    refused = identity.login("ALICE,pw")
    assert isinstance(refused, Refused)
    assert "ALREADY" in refused.reason
    identity.release(first)
    assert isinstance(identity.login("ALICE,pw"), Pilot)


def test_the_password_may_hold_commas_and_must_not_be_empty_or_huge() -> None:
    identity, _ = make()
    assert isinstance(identity.login("ALICE,a,b"), Pilot)
    identity.release(Pilot("ALICE", "ALICE"))
    assert isinstance(identity.login("ALICE,a,b"), Pilot)
    assert isinstance(identity.login("BOB,"), Refused)
    assert isinstance(identity.login("CAROL," + "x" * 64), Pilot)
    assert isinstance(identity.login("DAN," + "x" * 65), Refused)


def test_name_bounds() -> None:
    identity, _ = make()
    assert isinstance(identity.login(""), Refused)
    assert isinstance(identity.login("  ,pw"), Refused)
    assert isinstance(identity.login("N" * 33), Refused)
    assert isinstance(identity.login("N" * 32), Pilot)


def test_long_names_are_truncated_and_real_accounts_sharing_eight_characters_get_numbers() -> None:
    identity, _ = make()
    assert pilot(identity.login("LONGNAME1,a")).callsign == "LONGNAME"
    second = pilot(identity.login("LONGNAME2,b"))
    assert second.callsign == "LONGNAM2"
    assert second.account == "LONGNAME2"


def test_a_guest_cannot_take_a_registered_accounts_name() -> None:
    identity, _ = make()
    identity.release(pilot(identity.login("ALICE,pw")))
    refused = identity.login("alice")
    assert isinstance(refused, Refused)
    assert "REGISTERED" in refused.reason
    # An unregistered bare name is a guest named after itself.
    assert pilot(identity.login("zed")) == Pilot("ZED", None)


def test_a_guest_cannot_take_a_registered_accounts_callsign_by_truncation() -> None:
    identity, _ = make()
    identity.release(pilot(identity.login("ALPHAXYZ,secret")))
    refused = identity.login("ALPHAXYZW")
    assert isinstance(refused, Refused)
    assert pilot(identity.login("ALPHAXYZ,secret")).callsign == "ALPHAXYZ"


def test_a_guest_cannot_take_the_callsign_of_a_longer_registered_name() -> None:
    identity, _ = make()
    identity.release(pilot(identity.login("LONGNAME99,secret")))
    assert isinstance(identity.login("LONGNAME"), Refused)
    assert isinstance(identity.login("LONGNAMEX"), Refused)
    assert pilot(identity.login("LONGNAM")).callsign == "LONGNAM"


def test_joingenie_with_a_password_is_a_guest_and_registers_nothing() -> None:
    identity, store = make()
    guest = pilot(identity.login("joingenie,x"))
    assert guest.is_guest
    assert store.get("JOINGENIE") is None


def test_a_guest_online_makes_the_real_account_take_a_numbered_callsign() -> None:
    identity, _ = make()
    guest = pilot(identity.login("ALICE"))
    real = pilot(identity.login("ALICE,pw"))
    assert guest.callsign == "ALICE"
    assert real.callsign == "ALICE2"


def test_the_stored_hash_is_salted_pbkdf2_not_the_password() -> None:
    identity, store = make()
    identity.login("ALICE,hunter2")
    identity.login("BOB,hunter2")
    alice, bob = store.get("ALICE"), store.get("BOB")
    assert alice is not None and bob is not None
    assert alice.password_hash != b"hunter2"
    assert b"hunter2" not in alice.password_hash
    assert alice.salt != bob.salt  # a fresh salt per account
    assert alice.password_hash != bob.password_hash  # so equal passwords hash differently
    assert alice.password_hash == hash_password("hunter2", alice.salt, FAST)
    assert alice.iterations == FAST
    assert len(alice.password_hash) == 32


def test_hash_password_matches_the_pbkdf2_hmac_sha256_reference_vector() -> None:
    # RFC 7914 §11 test vector for PBKDF2-HMAC-SHA-256 (P="passwd", S="salt", c=1, dkLen=64).
    out = hash_password("passwd", b"salt", 1)
    assert out.hex().startswith("55ac046e56e3089fec1691c22544b605f94185216dde0465e68b9d57c20dacbc")


@pytest.mark.parametrize("line", ["ALICE,pw", "ALICE", "JOINGENIE"])
def test_release_of_an_unknown_pilot_is_harmless(line: str) -> None:
    identity, _ = make()
    identity.release(Pilot("NOBODY", None))
    assert isinstance(identity.login(line), Pilot)
