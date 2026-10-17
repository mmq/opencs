"""Server messages: exact bytes per Appendix A.1 codecs, round trips, and every validation rule."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from fixtures.wire_bytes import b10, b88, grid, num, string
from opencs import policy
from opencs.messages import DecodeError, WirePos, WireXZ, decode_server, server, server_payload
from opencs.messages.base import _SERVER, ServerMessage
from opencs.messages.catalog import CATALOG
from opencs.wire.frame import MAX_PAYLOAD, build_frame

TAG = 0x2A
T = bytes([TAG])

#: Cell (2, 3), Xoff 100, Y 72, Zoff -300: every number non-zero and a negative offset.
POS = WirePos(2, 3, 100, 72, -300)
POS_BYTES = grid(2, 3) + num(100) + num(72) + num(-300)
XZ = WireXZ(5, 6, 250, 333)
XZ_BYTES = grid(5, 6) + num(250) + num(333)

#: (message, expected payload) with every field non-zero where it can be, built from the codecs
#: Appendix A names. A message that used the wrong codec for a field fails here.
ENCODINGS: list[tuple[ServerMessage, bytes]] = [
    (server.ReleaseDrop(7), b"A" + T + b10(7)),
    (
        server.Session(3, 2, 1, b"ABC", b"NET", b"CITY", 8, 2, 3, 5, 6),
        b"B"
        + T
        + b10(3)
        + b10(17)
        + string(b"ABC")
        + string(b"NET")
        + string(b"CITY")
        + b10(8)
        + b10(2)
        + b10(3)
        + b10(5)
        + b10(6),
    ),
    (server.QuitAck(), b"C" + T),
    (server.SetStatus(1234, 5, 678), b"D" + T + b"S" + num(1234) + b10(5) + num(678)),
    (
        server.SetBays((1, 2, 3, 4, 5, 0, 1, 2, 3), -7),
        b"D" + T + b"B" + b"".join(b10(v) for v in (1, 2, 3, 4, 5, 0, 1, 2, 3)) + b88(-7),
    ),
    (server.AirSupportReply(9), b"E" + T + b"A" + num(9)),
    (server.SetBounty(5, 777), b"E" + T + b"B" + b10(5) + num(777)),
    (server.SendMuncher(300, 4097), b"E" + T + b"C" + num(300) + num(4097)),
    (
        server.GrenadeInFlight(POS, -869, 3332),
        b"E" + T + b"G" + POS_BYTES + num(-869) + num(3332),
    ),
    (server.KillNotice(True), b"E" + T + b"KE"),
    (server.KillNotice(False), b"E" + T + b"KN"),
    (server.PingReply(), b"E" + T + b"N"),
    (server.ModuleReply(26, 1), b"E" + T + b"P" + b10(26) + b10(1)),
    (server.EchoProbe(33), b"E" + T + b"c" + b10(33)),
    (server.SpawnMine(5000, 2, POS), b"F" + T + num(5000) + b10(2) + POS_BYTES),
    (
        server.Introduce(1, b"JOINGEN2", 3, 2, POS, 450, -5, 7, 9),
        b"G"
        + T
        + b10(1)
        + string(b"JOINGEN2")
        + b10(26)
        + POS_BYTES
        + num(450)
        + b88(-5)
        + num(7)
        + num(9),
    ),
    (server.Delete(200), b"H" + T + b10(200)),
    (server.Damage(70000, 23), b"I" + T + num(70000) + b10(23)),
    (server.Destroy(8200), b"J" + T + num(8200)),
    (server.MobileCreate(260, XZ), b"K" + T + num(260) + XZ_BYTES),
    (server.MobileWarp(251, XZ), b"L" + T + num(251) + XZ_BYTES),
    (
        server.SpawnTower(40, 9000, 3, POS, 1),
        b"M" + T + num(40) + num(9000) + b10(3) + POS_BYTES + b10(1),
    ),
    (server.SpawnSentinel(4100, XZ), b"N" + T + num(4100) + XZ_BYTES),
    (server.OrderReply(3), b"O" + T + b10(3)),
    (
        server.SpawnModule(8300, 21, POS, 1, -1, 2),
        b"P" + T + num(8300) + b10(21) + POS_BYTES + num(1) + num(-1) + num(2),
    ),
    (server.GrantPickup(32), b"Q" + T + b10(0) + num(32)),
    (server.InstallModule(30), b"Q" + T + b10(5) + num(30)),
    (server.DestroyObject(8300), b"Q" + T + b10(2) + num(8300)),
    (server.DestroyObject(8300, 9), b"Q" + T + b10(9) + num(8300)),
    (server.Ambient(2, 3), b"R" + T + b10(2) + b10(3)),
    (server.SetFlags(4, 8), b"S" + T + b10(4) + b10(8)),
    (
        server.SetBlocks(tuple(i % 4 for i in range(64))),
        b"T" + T + b"I" + b"".join(b10(i % 4) for i in range(64)),
    ),
    # Single cell: packed (Z << 4) | X, transposed from the position grid byte (I §8.22).
    (server.SetBlock(3, 5, 2), b"T" + T + b"C" + b10((5 << 4) | 3) + b10(2)),
    (server.SetBlock(7, 0, 1, sub=0x40), b"T" + T + b"\x40" + b10(7) + b10(1)),
    (server.Chat(100, 1, b"hi there"), b"a" + b10(100) + b10(1) + string(b"hi there")),
    (
        server.PodState(5, POS, 450, -3, 9, 11, 12, 1, 7),
        b"b"
        + b10(5)
        + POS_BYTES
        + num(450)
        + b88(-3)
        + b88(9)
        + num(11)
        + num(12)
        + b10(1)
        + b10(7),
    ),
    (
        server.Shot(3, POS, 3196, -45, 13),
        b"c" + num(3) + POS_BYTES + num(3196) + num(-45) + b10(13),
    ),
]


@pytest.mark.parametrize(("msg", "payload"), ENCODINGS, ids=lambda v: type(v).__name__)
def test_encoding_is_exactly_the_appendix_layout(msg: ServerMessage, payload: bytes) -> None:
    assert server_payload(msg, TAG) == payload


@pytest.mark.parametrize(("msg", "payload"), ENCODINGS, ids=lambda v: type(v).__name__)
def test_decoding_gives_the_message_and_its_tag(msg: ServerMessage, payload: bytes) -> None:
    decoded, tag = decode_server(payload)
    assert decoded == msg
    assert tag == (TAG if msg.is_tagged() else None)
    build_frame(payload)  # fits a frame


def test_every_server_class_is_covered_by_an_encoding() -> None:
    registered = set(_SERVER.classes.values())
    assert {type(m) for m, _ in ENCODINGS} == registered
    assert len(registered) == 34


# --- edge values ---------------------------------------------------------------------------


def test_edge_values_round_trip() -> None:
    far = WirePos(14, 15, -2000, -1, 99999)
    cases: list[ServerMessage] = [
        server.ReleaseDrop(0),
        server.ReleaseDrop(239),
        server.Delete(239),
        server.SetBounty(239, 0),
        server.SetBounty(0, 10**9),
        server.Destroy(0),
        server.Destroy(32767),
        server.Chat(0, 0, b""),
        server.Chat(239, 239, b" ~" * 115),
        server.PodState(0, far, -3599, -120, 119, -1, 1, 0, 239),
        server.PodState(239, far, 3599, 119, -120, 10**6, -(10**6), 1, 0),
        server.Introduce(239, b"A", 0, 0, far, 0),
        server.Session(239, 3, 3, b"X", b"N", b"C", 51, 2, 5, 239, 239),
        server.Session(0, 0, 0, b"ABCDEFGH", b"N" * 16, b"C" * 16, 1, 0, 0),
        server.Damage(0, 234),
        server.SetStatus(-1, 239, -(10**9)),
        server.SpawnModule(1, 47, far, -3, 4, 10**6),
    ]
    for msg in cases:
        payload = server_payload(msg, 0xFF)
        assert decode_server(payload) == (msg, 0xFF if msg.is_tagged() else None)


def test_a_230_byte_chat_fills_the_frame_exactly() -> None:
    msg = server.Chat(100, 0, b"x" * 230)
    payload = server_payload(msg, 0)
    assert len(payload) == MAX_PAYLOAD == 234
    build_frame(payload)
    assert decode_server(payload)[0] == msg


def test_kill_notice_any_other_byte_decodes_as_not_earned() -> None:
    for other in (b"N", b"X", b"e", b"\x10", b"\xff"):
        msg, _ = decode_server(b"E" + T + b"K" + other)
        assert msg == server.KillNotice(False)
    assert decode_server(b"E" + T + b"KE")[0] == server.KillNotice(True)
    assert server_payload(server.KillNotice(False), TAG)[-1] == policy.KILL_NOT_EARNED_BYTE


def test_q_subs_dispatch_and_any_other_is_destroy() -> None:
    assert decode_server(b"Q" + T + b10(0) + num(5))[0] == server.GrantPickup(5)
    assert decode_server(b"Q" + T + b10(5) + num(5))[0] == server.InstallModule(5)
    for sub in (1, 2, 3, 4, 6, 100, 239):
        assert decode_server(b"Q" + T + b10(sub) + num(77))[0] == server.DestroyObject(77, sub)


def test_t_sub_i_is_the_whole_grid_and_any_other_is_a_cell() -> None:
    assert isinstance(
        decode_server(server_payload(ENCODINGS_BY_TYPE[server.SetBlocks], TAG))[0], server.SetBlocks
    )
    for sub in (ord("C"), ord("Z"), 0x10, 0xFF):
        msg, _ = decode_server(b"T" + T + bytes([sub]) + b10(0x21) + b10(3))
        assert msg == server.SetBlock(1, 2, 3, sub)


ENCODINGS_BY_TYPE: dict[type[ServerMessage], ServerMessage] = {type(m): m for m, _ in ENCODINGS}


def test_set_blocks_world_order_is_8x_plus_z() -> None:
    classes = [0] * 64
    classes[8 * 2 + 5] = 3  # world block (X=2, Z=5)
    msg = server.SetBlocks(tuple(classes))
    assert msg.class_at(2, 5) == 3
    assert msg.class_at(5, 2) == 0
    payload = server_payload(msg, TAG)
    assert payload[3 + 8 * 2 + 5] == b10(3)[0]


def test_set_block_packs_z_high_and_x_low() -> None:
    body = server_payload(server.SetBlock(x=1, z=6, block_class=2), TAG)
    assert body[3] == 0x10 + 0x61  # Z=6 in the high nibble, X=1 in the low
    msg, _ = decode_server(body)
    assert (msg.x, msg.z) == (1, 6)  # type: ignore[attr-defined]


def test_damage_helpers() -> None:
    msg = server.Damage.of(1, 3, 4)
    assert (msg.damage, msg.magnitude, msg.weapon_class) == (34, 3, 4)


def test_shot_helpers() -> None:
    assert server.Shot(1, POS, 0, 0, 12).reduced_kind == 2
    assert server.Shot(1, POS, 0, 0, 9).reduced_kind == 9
    assert server.Shot(1, POS, 0, 0, 3).is_safe_kind


def test_sanitize_chat() -> None:
    assert server.sanitize_chat(b"hello") == b"hello"
    assert server.sanitize_chat(b"a\nb\rc\x0ed") == b"a b c d"
    assert server.sanitize_chat(b"\x01\x05\x7f\x80\xff") == b"     "
    assert server.sanitize_chat(b" ~") == b" ~"  # both printable edges kept
    assert server.sanitize_chat(b"x" * 300) == b"x" * 230
    assert server.sanitize_chat(b"") == b""
    server.Chat(0, 0, server.sanitize_chat(bytes(range(256)) * 2))  # always constructible


# --- validation: one test per rule, both sides of each boundary ------------------------------


def bad(factory: Callable[[], object]) -> None:
    with pytest.raises(ValueError):
        factory()


POD_ID_FIELDS: list[Callable[[int], object]] = [
    server.ReleaseDrop,
    server.Delete,
    lambda v: server.SetFlags(v, 8),
    lambda v: server.SetBounty(v, 5),
    lambda v: server.PodState(v, POS, 0, 0, 0, 0, 0, 0, 0),
    lambda v: server.Introduce(v, b"A", 0, 0, POS, 0),
    lambda v: server.Session(v, 0, 0, b"A", b"N", b"C", 1, 0, 0),
]


@pytest.mark.parametrize("make", POD_ID_FIELDS)
def test_pod_ids_are_0_to_239(make: Callable[[int], object]) -> None:
    make(0)
    make(239)
    bad(lambda: make(240))
    bad(lambda: make(-1))


@pytest.mark.parametrize("kind", [1, 2, 3, 9, 11, 12, 13])
def test_shot_kinds_the_client_survives(kind: int) -> None:
    assert server.Shot(1, POS, 0, 0, kind).is_safe_kind


@pytest.mark.parametrize("kind", [0, 4, 5, 6, 7, 8, 10, 14, 15, 16, 17, 18, 19, 20, 21, 100, 239])
def test_shot_kinds_that_crash_or_are_unknown_are_refused(kind: int) -> None:
    bad(lambda: server.Shot(1, POS, 0, 0, kind))
    with pytest.raises(DecodeError):
        decode_server(b"c" + num(1) + POS_BYTES + num(0) + num(0) + b10(kind))


@pytest.mark.parametrize(("sub", "high"), [(0, 4), (1, 4), (2, 3), (3, 3), (4, 1)])
def test_ambient_argument_ranges(sub: int, high: int) -> None:
    server.Ambient(sub, 0)
    server.Ambient(sub, high)
    bad(lambda: server.Ambient(sub, high + 1))
    bad(lambda: server.Ambient(sub, -1))


def test_ambient_sub_range() -> None:
    server.Ambient(4, 0)
    bad(lambda: server.Ambient(5, 0))
    bad(lambda: server.Ambient(-1, 0))


def test_block_classes_are_0_to_3() -> None:
    ok = [3] * 64
    server.SetBlocks(tuple(ok))
    for i in (0, 63):
        for value in (4, -1, 0xF0 - 0x10):
            broken = list(ok)
            broken[i] = value
            bad(lambda broken=broken: server.SetBlocks(tuple(broken)))
    bad(lambda: server.SetBlocks(tuple([0] * 63)))
    bad(lambda: server.SetBlocks(tuple([0] * 65)))
    server.SetBlock(0, 0, 3)
    bad(lambda: server.SetBlock(0, 0, 4))
    bad(lambda: server.SetBlock(0, 0, -1))


def test_single_cell_coordinates_are_0_to_7() -> None:
    server.SetBlock(7, 7, 0)
    for x, z in ((8, 0), (0, 8), (-1, 0), (0, -1), (15, 15)):
        bad(lambda x=x, z=z: server.SetBlock(x, z, 0))


def test_single_cell_sub_is_not_the_grid_letter() -> None:
    bad(lambda: server.SetBlock(1, 1, 1, sub=ord("I")))
    server.SetBlock(1, 1, 1, sub=ord("H"))
    server.SetBlock(1, 1, 1, sub=ord("J"))
    for sub in (0x0F, 0x00, 0x100):
        bad(lambda sub=sub: server.SetBlock(1, 1, 1, sub=sub))


def test_a_short_whole_grid_is_not_decoded_as_a_cell() -> None:
    # 'I' + 63 bytes is a short grid, not a cell
    with pytest.raises(DecodeError):
        decode_server(b"T" + T + b"I" + b10(0) * 63)


GOOD_CHAT = b"abc XYZ ~!"


@pytest.mark.parametrize("byte", [0x00, 0x0A, 0x0D, 0x0E, 0x01, 0x05, 0x1F, 0x7F, 0x80, 0xFF])
def test_chat_refuses_bytes_outside_printable_ascii(byte: int) -> None:
    bad(lambda: server.Chat(0, 0, b"a" + bytes([byte]) + b"b"))


@pytest.mark.parametrize("byte", [0x20, 0x7E])
def test_chat_accepts_the_printable_edges(byte: int) -> None:
    server.Chat(0, 0, bytes([byte]))


def test_chat_length_limit() -> None:
    server.Chat(0, 0, b"x" * 230)
    bad(lambda: server.Chat(0, 0, b"x" * 231))


def test_chat_sender_and_style_are_bytes() -> None:
    server.Chat(policy.SYSTEM_SENDER, 239, b"x")
    for v in (240, -1):
        bad(lambda v=v: server.Chat(v, 0, b"x"))
        bad(lambda v=v: server.Chat(0, v, b"x"))


def test_pod_state_state_is_0_or_1() -> None:
    def make(state: int) -> server.PodState:
        return server.PodState(1, POS, 0, 0, 0, 0, 0, state, 0)

    make(0)
    make(1)
    for state in (2, 3, 4, 5, -1):
        bad(lambda state=state: make(state))
    with pytest.raises(DecodeError):  # a server 'b' with state 4 never decodes
        decode_server(
            b"b" + b10(1) + POS_BYTES + num(0) + b88(0) + b88(0) + num(0) + num(0) + b10(4) + b10(0)
        )


def test_pod_state_speeds_stay_in_bias88_range() -> None:
    def make(fwd: int, target: int) -> server.PodState:
        return server.PodState(1, POS, 0, fwd, target, 0, 0, 0, 0)

    make(-120, 119)
    make(119, -120)
    for fwd, target in ((120, 0), (-121, 0), (0, 120), (0, -121)):
        bad(lambda fwd=fwd, target=target: make(fwd, target))


def test_pod_state_weight_and_position_cells() -> None:
    bad(lambda: server.PodState(1, POS, 0, 0, 0, 0, 0, 0, 240))
    bad(lambda: server.PodState(1, POS, 0, 0, 0, 0, 0, 0, -1))
    bad(lambda: WirePos(15, 0, 0, 0, 0))  # grid byte nibble out of what a byte holds
    bad(lambda: WirePos(0, 16, 0, 0, 0))
    bad(lambda: WireXZ(-1, 0, 0, 0))


SESSION = server.Session(3, 2, 1, b"ABC", b"NET", b"CITY", 8, 2, 3)


def test_session_slot_parts() -> None:
    for index in (0, 3):
        for team in (0, 3):
            replace(SESSION, index=index, team=team)
    for index, team in ((4, 0), (-1, 0), (0, 4), (0, -1)):
        bad(lambda index=index, team=team: replace(SESSION, index=index, team=team))


def test_session_string_limits() -> None:
    replace(SESSION, callsign=b"A" * 8, network=b"N" * 16, city=b"C" * 16)
    bad(lambda: replace(SESSION, callsign=b"A" * 9))
    bad(lambda: replace(SESSION, network=b"N" * 17))
    bad(lambda: replace(SESSION, city=b"C" * 17))
    bad(lambda: replace(SESSION, callsign=b""))
    bad(lambda: replace(SESSION, network=b""))
    bad(lambda: replace(SESSION, city=b""))


def test_session_strings_are_printable_and_callsign_upper_case() -> None:
    for field in ("callsign", "network", "city"):
        for byte in (0x01, 0x05, 0x0D, 0x7F, 0xE9):
            bad(lambda field=field, byte=byte: replace(SESSION, **{field: b"A" + bytes([byte])}))
    bad(lambda: replace(SESSION, callsign=b"Joe"))
    replace(SESSION, callsign=b"AB 12~!")
    replace(SESSION, city=b"Cyber City")  # only the callsign must be upper case


def test_session_city_settings() -> None:
    replace(SESSION, max_mult=1)
    replace(SESSION, max_mult=51)
    for v in (0, 52, -1):
        bad(lambda v=v: replace(SESSION, max_mult=v))
    replace(SESSION, tower_output=0)
    replace(SESSION, tower_output=2)
    for v in (3, -1):
        bad(lambda v=v: replace(SESSION, tower_output=v))
    replace(SESSION, max_module_level=0)
    replace(SESSION, max_module_level=5)
    for v in (6, -1):
        bad(lambda v=v: replace(SESSION, max_module_level=v))
    for field in ("n4", "n5"):
        replace(SESSION, **{field: 239})
        bad(lambda field=field: replace(SESSION, **{field: 240}))


def test_session_decode_rejects_slot_parts_out_of_range() -> None:
    good = server_payload(SESSION, TAG)
    assert decode_server(good)[0] == SESSION
    for slot in (b10(4 << 3), b10(4), b10(7)):  # index 4, team 4, team 7
        bad_slot = good[:3] + slot + good[4:]
        with pytest.raises(DecodeError):
            decode_server(bad_slot)


def test_bays_levels_marker_and_count() -> None:
    levels = (0, 5, 1, 2, 3, 4, 5, 0, 1)
    server.SetBays(levels, -120)
    server.SetBays(levels, 119)
    bad(lambda: server.SetBays(levels, 120))
    bad(lambda: server.SetBays(levels, -121))
    for i in (0, 8):
        for value in (6, -1):
            broken = list(levels)
            broken[i] = value
            bad(lambda broken=broken: server.SetBays(tuple(broken), 0))
    bad(lambda: server.SetBays(levels[:8], 0))
    bad(lambda: server.SetBays((*levels, 1), 0))


def test_status_multiplier_is_a_byte() -> None:
    server.SetStatus(0, 239, 0)
    bad(lambda: server.SetStatus(0, 240, 0))
    bad(lambda: server.SetStatus(0, -1, 0))


MODULE_FIELDS: list[Callable[[int], object]] = [
    lambda v: server.ModuleReply(v, 1),
    lambda v: server.SpawnModule(1, v, POS),
    server.InstallModule,
]


@pytest.mark.parametrize("make", MODULE_FIELDS)
def test_module_indices_must_be_in_the_catalog(make: Callable[[int], object]) -> None:
    for index in CATALOG:
        make(index)
    for index in (0, 10, 19, 20, 38, 39, 40, 48, 100, 239, -1):
        bad(lambda index=index: make(index))


def test_module_reply_ok_is_a_byte() -> None:
    server.ModuleReply(1, 0)
    server.ModuleReply(1, 239)
    bad(lambda: server.ModuleReply(1, 240))
    bad(lambda: server.ModuleReply(1, -1))


def test_destroy_object_sub_is_neither_grant_nor_install() -> None:
    for sub in (1, 2, 3, 4, 6, 239):
        server.DestroyObject(1, sub)
    for sub in (0, 5, 240, -1):
        bad(lambda sub=sub: server.DestroyObject(1, sub))
    with pytest.raises(DecodeError):  # a raw sub byte below 0x10 is not a b10
        decode_server(b"Q" + T + b"\x05" + num(1))


def test_tower_team_and_mega() -> None:
    server.SpawnTower(0, 1, 3, POS, 1)
    server.SpawnTower(0, 1, 0, POS, 0)
    bad(lambda: server.SpawnTower(0, 1, 4, POS, 0))
    bad(lambda: server.SpawnTower(0, 1, -1, POS, 0))
    bad(lambda: server.SpawnTower(0, 1, 0, POS, 2))
    bad(lambda: server.SpawnTower(0, 1, 0, POS, -1))


def test_mine_variant_is_a_team_model() -> None:
    server.SpawnMine(1, 3, POS)
    bad(lambda: server.SpawnMine(1, 4, POS))
    bad(lambda: server.SpawnMine(1, -1, POS))


def test_order_reply_range() -> None:
    server.OrderReply(0)
    server.OrderReply(4)
    bad(lambda: server.OrderReply(5))
    bad(lambda: server.OrderReply(-1))


def test_damage_classes_are_0_to_4() -> None:
    for damage in (0, 4, 10, 34, 234):
        server.Damage(0, damage)
    for damage in (5, 9, 15, 239, 240, -1):
        bad(lambda damage=damage: server.Damage(0, damage))
    with pytest.raises(DecodeError):
        decode_server(b"I" + T + num(0) + b10(7))


def test_introduce_callsign_and_velocity() -> None:
    def make(callsign: bytes = b"A", v1: int = 0) -> server.Introduce:
        return server.Introduce(1, callsign, 0, 0, POS, 0, v1)

    make(b"A" * 8)
    bad(lambda: make(b"A" * 9))
    bad(lambda: make(b""))
    bad(lambda: make(b"A\n"))
    bad(lambda: make(b"Joe"))  # one rule for 'B' and 'G': no lower case
    bad(lambda: make(v1=120))
    bad(lambda: replace(make(), index=4))
    bad(lambda: replace(make(), team=4))


def test_echo_probe_and_flags_are_bytes() -> None:
    server.EchoProbe(239)
    bad(lambda: server.EchoProbe(240))
    server.SetFlags(0, 239)
    bad(lambda: server.SetFlags(0, 240))
    bad(lambda: server.SetFlags(0, -1))


def test_messages_are_frozen() -> None:
    msg = server.Delete(3)
    with pytest.raises((AttributeError, TypeError)):
        msg.id = 4  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        msg.extra = 1  # type: ignore[attr-defined]
