"""Identity: real accounts, JOINGENIE guests, callsigns (II §3, II §3)."""

from __future__ import annotations

import pytest

from opencs.lobby.identity import Identity, Pilot, Refused, hash_password, identity_key
from opencs.store.memory import MemoryAccountStore

FAST = 1000  # PBKDF2 rounds: the algorithm is the point, not the cost


def make(guests: tuple[str, ...] = ("JOINGENIE",)) -> tuple[Identity, MemoryAccountStore]:
    store = MemoryAccountStore()
    return Identity(store, guest_names=guests, iterations=FAST), store


def pilot(result: Pilot | Refused) -> Pilot:
    assert isinstance(result, Pilot), result
    return result


def test_joingenie_callsigns_are_the_name_then_numbered() -> None:
    identity, _ = make()
    names = [pilot(identity.login("JOINGENIE")).callsign for _ in range(3)]
    assert names == ["JOINGENIE", "JOINGENIE2", "JOINGENIE3"]


def test_joingenie_is_case_insensitive_and_has_no_password() -> None:
    identity, _ = make()
    first = pilot(identity.login("joingenie"))
    assert first == Pilot("JOINGENIE", None)
    assert first.is_guest
    # A password after JOINGENIE is not a registration of the guest name.
    assert pilot(identity.login("JoinGenie,secret")).callsign == "JOINGENIE2"


def test_a_freed_callsign_is_reused() -> None:
    identity, _ = make()
    a = pilot(identity.login("JOINGENIE"))
    b = pilot(identity.login("JOINGENIE"))
    identity.release(a)
    assert pilot(identity.login("JOINGENIE")).callsign == "JOINGENIE"
    assert identity.callsign_online(b.callsign)


def test_later_duplicates_get_numbers_up_to_999_then_are_refused() -> None:
    identity, _ = make()
    calls = [pilot(identity.login("JOINGENIE")).callsign for _ in range(999)]
    assert calls[8:10] == ["JOINGENIE9", "JOINGENIE10"]
    assert calls[98:100] == ["JOINGENIE99", "JOINGENIE100"]
    assert calls[-1] == "JOINGENIE999"
    assert all(len(c) <= 12 for c in calls)
    assert isinstance(identity.login("JOINGENIE"), Refused)


def test_a_twelve_character_name_gives_up_its_tail_to_the_number() -> None:
    identity, _ = make(("ABCDEFGHIJKLM",))
    calls = [pilot(identity.login("ABCDEFGHIJKLM")).callsign for _ in range(101)]
    assert calls[:2] == ["ABCDEFGHIJKL", "ABCDEFGHIJK2"]
    assert calls[9] == "ABCDEFGHIJ10"
    assert calls[100] == "ABCDEFGHI101"


def test_short_names_get_the_digit_appended() -> None:
    identity, _ = make(("JOINGENIE", "BOB"))
    assert pilot(identity.login("BOB")).callsign == "BOB"
    assert pilot(identity.login("bob")).callsign == "BOB2"


def test_a_bare_name_that_is_not_a_guest_name_needs_a_password() -> None:
    identity, store = make()
    refused = identity.login("ALICE")
    assert isinstance(refused, Refused) and "PASSWORD" in refused.reason
    assert store.get("ALICE") is None


def test_configured_guest_names_ignore_a_password_and_may_be_shared() -> None:
    identity, store = make(("JOINGENIE", "visitor"))
    first, second = pilot(identity.login("VISITOR,one")), pilot(identity.login("Visitor,two"))
    assert (first, second) == (Pilot("VISITOR", None), Pilot("VISITOR2", None))
    assert store.get("VISITOR") is None


def test_with_no_guest_names_joingenie_is_refused_and_registers_nothing() -> None:
    identity, store = make(())
    assert isinstance(identity.login("JOINGENIE"), Refused)
    assert isinstance(identity.login("JOINGENIE,pw"), Refused)
    assert store.get("JOINGENIE") is None
    assert pilot(identity.login("ALICE,pw")).account == "ALICE"


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
    assert isinstance(identity.login("N" * 33 + ",pw"), Refused)
    assert isinstance(identity.login("N" * 32 + ",pw"), Pilot)


def test_long_names_are_truncated_and_real_accounts_sharing_twelve_characters_get_numbers() -> None:
    identity, _ = make()
    assert pilot(identity.login("LONGERNAME001,a")).callsign == "LONGERNAME00"
    second = pilot(identity.login("LONGERNAME002,b"))
    assert second.callsign == "LONGERNAME02"
    assert second.account == "LONGERNAME002"


def test_a_guest_cannot_take_a_registered_accounts_name() -> None:
    identity, store = make()
    identity.release(pilot(identity.login("ALICE,pw")))
    guests = Identity(store, guest_names=("ALICE", "ZED"), iterations=FAST)
    refused = guests.login("alice")
    assert isinstance(refused, Refused)
    assert "REGISTERED" in refused.reason
    # An unregistered guest name is a guest named after itself.
    assert pilot(guests.login("zed")) == Pilot("ZED", None)


def test_a_guest_cannot_take_a_registered_accounts_callsign_by_truncation() -> None:
    identity, _ = make(("ALPHABRAVOXYW",))
    identity.release(pilot(identity.login("ALPHABRAVOXY,secret")))
    refused = identity.login("ALPHABRAVOXYW")
    assert isinstance(refused, Refused) and "REGISTERED" in refused.reason
    assert pilot(identity.login("ALPHABRAVOXY,secret")).callsign == "ALPHABRAVOXY"


def test_a_guest_cannot_take_the_callsign_of_a_longer_registered_name() -> None:
    identity, _ = make(("LONGERNAME00", "LONGERNAME00X", "LONGERNAME0"))
    identity.release(pilot(identity.login("LONGERNAME0099,secret")))
    assert isinstance(identity.login("LONGERNAME00"), Refused)
    assert isinstance(identity.login("LONGERNAME00X"), Refused)
    assert pilot(identity.login("LONGERNAME0")).callsign == "LONGERNAME0"


def test_joingenie_with_a_password_is_a_guest_and_registers_nothing() -> None:
    identity, store = make()
    guest = pilot(identity.login("joingenie,x"))
    assert guest.is_guest
    assert store.get("JOINGENIE") is None


def test_a_guest_online_makes_the_real_account_take_a_numbered_callsign() -> None:
    identity, _ = make(("VISITORVISITOR",))
    guest = pilot(identity.login("VISITORVISITOR"))
    real = pilot(identity.login("VISITORVISITX,pw"))
    assert guest.callsign == "VISITORVISIT"
    assert real.callsign == "VISITORVISI2"


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


@pytest.mark.parametrize("line", ["ALICE,pw", "JOINGENIE"])
def test_release_of_an_unknown_pilot_is_harmless(line: str) -> None:
    identity, _ = make()
    identity.release(Pilot("NOBODY", None))
    assert isinstance(identity.login(line), Pilot)


# --- progress keys (II §3) ---------------------------------------------------


def test_a_real_account_is_keyed_by_the_account_not_the_callsign() -> None:
    identity, _ = make()
    first = pilot(identity.login("ALPHABRAVOXY1,pw"))
    second = pilot(identity.login("ALPHABRAVOXY2,pw"))
    assert (first.callsign, second.callsign) == ("ALPHABRAVOXY", "ALPHABRAVOX2")
    assert (first.key, second.key) == ("acct:ALPHABRAVOXY1", "acct:ALPHABRAVOXY2")


def test_a_real_account_keeps_its_key_when_a_guest_holds_its_callsign() -> None:
    identity, _ = make()
    identity.release(pilot(identity.login("ZED,pw")))  # registered
    guest = pilot(identity.login("JOINGENIE"))
    squatter = pilot(identity.login("ZED,pw"))
    assert squatter.key == "acct:ZED" and guest.key == "JOINGENIE"
    identity.release(squatter)
    identity.release(guest)
    other = pilot(identity.login("ZEDZEDZEDZEDZ,pw"))  # callsign ZEDZEDZEDZED, another account
    assert pilot(identity.login("ZED,pw")).key == "acct:ZED" and other.key == "acct:ZEDZEDZEDZEDZ"


def test_joingenie_is_keyed_by_the_assigned_callsign_in_connection_order() -> None:
    identity, _ = make()
    first, second, third = (pilot(identity.login("JOINGENIE")) for _ in range(3))
    assert [first.key, second.key, third.key] == ["JOINGENIE", "JOINGENIE2", "JOINGENIE3"]
    identity.release(second)
    assert pilot(identity.login("JOINGENIE")).key == "JOINGENIE2"  # a freed callsign is a free key


def test_a_guest_key_cannot_equal_an_account_key() -> None:
    identity, _ = make(("acct:zed",))
    guest = pilot(identity.login("acct:zed"))  # upper-cased, so never the lower-case prefix
    assert guest.key == "ACCT:ZED"
    assert identity_key("ZED", "ZED") == "acct:ZED" and identity_key("ZED", None) == "ZED"
