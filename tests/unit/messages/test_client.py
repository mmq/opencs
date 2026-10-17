"""Client messages: exact bytes per Appendix A.2 codecs, round trips, hostile values accepted."""

from __future__ import annotations

import pytest

from fixtures.wire_bytes import b10, b88, grid, num, string
from opencs.messages import DecodeError, WirePos, WireXZ, client, client_payload, decode_client
from opencs.messages.base import _CLIENT, ClientMessage
from opencs.wire.codec import ENC_NUM_MAX

POS = WirePos(2, 3, 100, 72, -300)
POS_BYTES = grid(2, 3) + num(100) + num(72) + num(-300)
XZ = WireXZ(5, 6, 250, 333)
XZ_BYTES = grid(5, 6) + num(250) + num(333)
LEVELS = (1, 2, 3, 4, 5, 0, 1, 2, 3)

ENCODINGS: list[tuple[ClientMessage, bytes]] = [
    (client.DropMe(), b"A"),
    (client.Quit(), b"C"),
    (client.Status(1234, 5, 678), b"DS" + num(1234) + b10(5) + num(678)),
    (client.Bays(LEVELS, -7), b"DB" + b"".join(b10(v) for v in LEVELS) + b88(-7)),
    *[(client.EffectRequest(c), b"E" + c.encode()) for c in "RWHLMCSID"],
    (client.AirSupport(7, XZ), b"EA" + b10(7) + XZ_BYTES),
    (client.Grenade(POS, 869, -3332), b"EG" + POS_BYTES + num(869) + num(-3332)),
    (client.Ping(), b"EN"),
    (client.ModuleOrder(26, 3, 4), b"EP" + b10(26) + b10(3) + b10(4)),
    (client.EchoReply(33, 4097), b"Ec" + b10(33) + num(4097)),
    (client.Drone(), b"EdO"),
    (client.Drone(XZ), b"EdM" + XZ_BYTES),
    (client.HitReport(4096, 23), b"I" + num(4096) + b10(23)),
    (client.Died(), b"J"),
    (client.LegArrived(250), b"K" + num(250)),
    (client.TowerOrder(POS, 1, 5), b"M" + POS_BYTES + b10(1) + b10(5)),
    (client.PickupRequest(8300), b"Q" + b10(0) + num(8300)),
    (client.EjectRequest(26), b"Q" + b10(1) + num(26)),
    (client.ModuleRested(8300, POS), b"Q" + b10(3) + num(8300) + POS_BYTES),
    (client.ModuleUsedUp(30), b"Q" + b10(4) + num(30)),
    (client.Flags(8), b"S" + b10(8)),
    (client.Chat(1, b"hi there"), b"U" + b10(1) + string(b"hi there")),
    (
        client.PodState(POS, 450, -3, 9, 11, -12, 1, 3000, 7, 40, 50),
        b"b" + POS_BYTES + num(450) + b88(-3) + b88(9) + num(11) + num(-12) + b10(1)
        + num(3000) + b10(7) + num(40) + num(50),
    ),
    (
        client.Shot(3, POS, 3196, -45, 11),
        b"c" + num(3) + POS_BYTES + num(3196) + num(-45) + b10(11),
    ),
    (client.Acks(b"\x11\x12\x13\x14\x15"), b"d\x11\x12\x13\x14\x15"),
]  # fmt: skip


@pytest.mark.parametrize(("msg", "payload"), ENCODINGS, ids=lambda v: repr(v)[:40])
def test_encoding_is_exactly_the_appendix_layout(msg: ClientMessage, payload: bytes) -> None:
    assert client_payload(msg) == payload
    assert decode_client(payload) == msg


@pytest.mark.parametrize(("msg", "payload"), ENCODINGS, ids=lambda v: repr(v)[:40])
def test_trailing_bytes_are_ignored(msg: ClientMessage, payload: bytes) -> None:
    if isinstance(msg, client.Acks):
        return  # takes the rest of the payload by definition
    assert decode_client(payload + b"\x10\x11") == msg


def test_every_client_class_has_an_encoding() -> None:
    assert {type(m) for m, _ in ENCODINGS} == set(_CLIENT.classes.values())
    assert len(set(_CLIENT.classes.values())) == 24


def test_every_effect_letter_maps_to_a_request_and_the_unused_letters_never_arrive() -> None:
    assert set(client.EFFECTS) == set("RWHLMCSID")
    for letter in client.EFFECTS:
        assert decode_client(b"E" + letter.encode()) == client.EffectRequest(letter)
    for letter in ("V", "v", "X", "Z", "a", "\x10"):
        with pytest.raises(DecodeError):
            decode_client(b"E" + letter.encode())


def test_q_subs_0_1_3_4_only() -> None:
    for sub in (2, 5, 6, 100, 239):
        with pytest.raises(DecodeError):
            decode_client(b"Q" + b10(sub) + num(1))
    with pytest.raises(DecodeError):
        decode_client(b"Q\x05" + num(1))  # not a b10


def test_drone_forms() -> None:
    assert decode_client(b"EdO") == client.Drone(None)
    for form in (b"X", b"o", b"m", b"\x10"):
        with pytest.raises(DecodeError):
            decode_client(b"Ed" + form)
    with pytest.raises(DecodeError):
        decode_client(b"Ed")
    with pytest.raises(DecodeError):
        decode_client(b"EdM" + grid(1, 1) + num(5))  # no Zoff


def test_acks_take_any_count() -> None:
    assert decode_client(b"d") == client.Acks(b"")
    assert decode_client(b"d" + bytes(range(0x11, 0x1C))) == client.Acks(bytes(range(0x11, 0x1C)))
    assert client_payload(client.Acks(b"")) == b"d"


def test_hostile_values_decode_without_validation() -> None:
    # The client is the authority on its own format; sessions judge the values (M3+).
    weird = decode_client(
        b"b" + POS_BYTES + num(0) + b88(0) + b88(0) + num(0) + num(0) + b10(4) + num(-5)
        + b10(239) + num(0) + num(0)
    )  # fmt: skip
    assert isinstance(weird, client.PodState)
    assert (weird.state, weird.power, weird.weight) == (4, -5, 239)
    assert decode_client(b"I" + num(1) + b10(239)) == client.HitReport(1, 239)
    assert decode_client(b"U" + b10(239) + string(b"\x0a\x0d\x0e\x01")) == client.Chat(
        239, b"\x0a\x0d\x0e\x01"
    )  # raw text is kept; the session sanitizes it


@pytest.mark.parametrize(
    ("kind", "reduced", "safe"),
    [
        (0, 0, False),
        (1, 1, True),
        (2, 2, True),
        (3, 3, True),
        (4, 4, False),
        (9, 9, True),
        (10, 0, False),
        (11, 1, True),
        (12, 2, True),
        (13, 3, True),
        (14, 4, False),
        (19, 9, False),  # I §8.25: 19 is UNKNOWN, so not safe to relay
        (20, 10, False),
        (239, 229, False),
    ],
)
def test_client_shot_exposes_the_kind_facts(kind: int, reduced: int, safe: bool) -> None:
    shot = decode_client(b"c" + num(1) + POS_BYTES + num(0) + num(0) + b10(kind))
    assert isinstance(shot, client.Shot)
    assert shot.kind == kind
    assert shot.reduced_kind == reduced
    assert shot.is_safe_kind is safe


def test_hit_report_damage_facts() -> None:
    hit = client.HitReport(1, 34)
    assert (hit.magnitude, hit.weapon_class) == (3, 4)


def test_edge_values_round_trip() -> None:
    far = WirePos(14, 15, -2000, -1, 99999)
    cases: list[ClientMessage] = [
        client.PodState(far, -3599, -120, 119, 10**6, -(10**6), 4, 0, 239, 0, 0),
        client.PodState(far, 0, 0, 0, 0, 0, 0, ENC_NUM_MAX, 0, 0, 0),
        client.Status(-(10**9), 239, 10**9),
        client.Shot(32767, far, 0, 0, 239),
        client.Chat(0, b""),
        client.Chat(239, b"x" * 239),
        client.Bays((0,) * 9, -120),
        client.Bays((5,) * 9, 119),
        client.LegArrived(0),
        client.AirSupport(239, WireXZ(14, 15, -5, 5)),
    ]
    for msg in cases:
        assert decode_client(client_payload(msg)) == msg


def test_b88_scalars_use_bias_88_not_10() -> None:
    # fwd 0 is 0x88, a pod at rest; reading it as bias10 would give 120.
    pod = decode_client(
        b"b" + POS_BYTES + num(0) + b"\x88\x88" + num(0) + num(0) + b10(0) + num(0) + b10(0)
        + num(0) + num(0)
    )  # fmt: skip
    assert isinstance(pod, client.PodState)
    assert (pod.fwd, pod.fwd_target) == (0, 0)
    bays = decode_client(b"DB" + b10(1) * 9 + b"\x91")
    assert isinstance(bays, client.Bays)
    assert bays.marker == 9
