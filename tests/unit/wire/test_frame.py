"""Frame build/parse against the spec's real captures (I §4.1, I App. A.1, I App. A.2) and hostile
input."""

from __future__ import annotations

import random

import pytest

from fixtures.spec_vectors import FULL_FRAMES, PAYLOADS, h
from opencs.wire.codec import Reader
from opencs.wire.errors import WireError
from opencs.wire.frame import MAX_PAYLOAD, MAX_PENDING, Frame, FrameReader, build_frame


def parse_one(data: bytes) -> Frame:
    reader = FrameReader()
    frames = reader.feed(data)
    assert len(frames) == 1
    assert reader.bad_frames == 0
    assert reader.skipped_bytes == 0
    return frames[0]


@pytest.mark.parametrize("name", sorted(FULL_FRAMES))
def test_every_spec_frame_round_trips(name: str) -> None:
    _, raw = FULL_FRAMES[name]
    frame = parse_one(raw)
    # ``raw`` is the frame proper; a payload never ends in a terminator byte, so this is exact.
    term = raw[len(raw) - 1 :] if raw[-1] in (0x0D, 0x0E) else b""
    assert frame.raw == raw[: len(raw) - len(term)]
    assert build_frame(frame.payload, terminator=term) == raw


@pytest.mark.parametrize("name", sorted(FULL_FRAMES))
def test_every_spec_frame_splits_at_every_boundary(name: str) -> None:
    _, raw = FULL_FRAMES[name]
    for cut in range(len(raw) + 1):
        reader = FrameReader()
        frames = reader.feed(raw[:cut]) + reader.feed(raw[cut:])
        assert [f.payload for f in frames] == [parse_one(raw).payload]
        assert reader.skipped_bytes == 0


def test_one_byte_feeds() -> None:
    raws = [raw for _, raw in FULL_FRAMES.values()]
    reader = FrameReader()
    got: list[Frame] = []
    for b in b"".join(raws):
        got += reader.feed(bytes([b]))
    assert [f.raw for f in got] == [r.rstrip(b"\r\x0e") if r[-1] in (13, 14) else r for r in raws]
    assert reader.skipped_bytes == 0
    assert reader.frames == len(raws)


def test_payload_only_vectors_build_and_parse() -> None:
    for payload in PAYLOADS.values():
        assert parse_one(build_frame(payload)).payload == payload


def test_type_and_tag_properties() -> None:
    b = parse_one(FULL_FRAMES["A3_B"][1])
    assert (b.type, b.is_upper, b.payload[1]) == ("B", True, 0x11)
    s = parse_one(FULL_FRAMES["A3_b"][1])
    assert (s.type, s.is_upper) == ("b", False)
    assert parse_one(FULL_FRAMES["A3_A"][1]).payload[1] == 0x20  # tag 20


# Field-by-field decodes of every A.3 / A.3b vector, with the codec Appendix A.1/A.2 names for each
# field (I §4.3: the wrong codec is the commonest error; zero hides it, so non-zero values matter).
# ops: r = one raw byte (type, tag, sub-type), others are the codec names.
_Ops = tuple[str, ...]
FIELD_CASES: list[tuple[str, _Ops, tuple[object, ...]]] = [
    # A.3
    (
        "A3_B",  # type, tag, self_id, slot, callsign, network, city, max_mult, tower_output,
        # max_module_level, n4, n5
        ("r", "r", "b10", "b10", "str", "str", "str", "b10", "b10", "b10", "b10", "b10"),
        (b"B", b"\x11", 0, 0, b"JOINGENI", b"GENIE", b"CYBERCITY ONE", 8, 1, 2, 0, 0),
    ),
    ("A3_S", ("r", "b10"), (b"S", 0)),
    ("A3_A", ("r", "r", "b10"), (b"A", b"\x20", 0)),  # tag 20, id 0
    (
        "A3_b",  # grid, Xoff, Y, Zoff, orient, fwd, fwd_target, v1, v2, state, power, ...
        (
            *("r", "grid", "num", "num", "num", "num", "b88", "b88", "num", "num", "b10"),
            *("num", "b10", "num", "num"),
        ),
        (b"b", (0, 0), 145, 240, 5, 450, 0, 0, 0, 0, 4, 3000, 0, 0, 0),
    ),
    ("A3_EN", ("r", "r"), (b"E", b"N")),
    ("A3_EN_reply", ("r", "r", "r"), (b"E", b"\x27", b"N")),  # tag 27
    ("A3_d", ("r", "rest"), (b"d", bytes([0x11, 0x12, 0x13, 0x14, 0x15]))),
    ("A3_I", ("r", "num", "b10"), (b"I", 4096, 0)),  # the Sentinel, damage class 0
    (
        "A3_M",  # grid, Xoff, Y, Zoff, mega, priority
        ("r", "grid", "num", "num", "num", "b10", "b10"),
        (b"M", (0, 0), 224, 62, 332, 0, 0),
    ),
    ("A3_K", ("r", "num"), (b"K", 250)),
    ("A3_Q", ("r", "b10", "num"), (b"Q", 0, 32)),  # sub 0, object 32
    # A.3b
    (
        "A3b_G",  # id, callsign, slot, grid, Xoff, Y, Zoff, orient, v1:b88, v2, bounty_div10
        ("r", "r", "b10", "str", "b10", "grid", "num", "num", "num", "num", "b88", "num", "num"),
        (b"G", b"\x3a", 1, b"JOINGEN2", 8, (0, 0), 75, 240, 75, 450, 0, 0, 0),
    ),
    ("A3b_A", ("r", "r", "b10"), (b"A", b"\x3b", 1)),
    (
        "A3b_b",  # S->C relayed: id first, then as A.1 'b' (no power or armor)
        (
            *("r", "b10", "grid", "num", "num", "num", "num", "b88", "b88", "num", "num"),
            *("b10", "b10"),
        ),
        (b"b", 1, (0, 0), 75, 240, 75, 450, 0, 0, 0, 0, 0, 0),
    ),
    (
        "A3b_E_grenade_c2s",
        ("r", "r", "grid", "num", "num", "num", "num", "num"),
        (b"E", b"G", (2, 2), 100, 72, 300, 869, 3332),
    ),
    (
        "A3b_E_grenade_s2c",
        ("r", "r", "r", "grid", "num", "num", "num", "num", "num"),
        (b"E", b"\x87", b"G", (2, 2), 100, 72, 300, 869, 3332),
    ),
    ("A3b_J_s2c", ("r", "r", "num"), (b"J", b"\x4d", 1)),  # tag 4d, id num 20 -> 1
    ("A3b_J_c2s", ("r",), (b"J",)),
    ("A3b_J0_s2c", ("r", "r", "num"), (b"J", b"\x4f", 0)),
    ("A3b_A_c2s", ("r",), (b"A",)),
    ("A3b_U", ("r", "b10", "str"), (b"U", 0, b"ok")),
    ("A3b_a", ("r", "b10", "b10", "str"), (b"a", 0, 0, b"ok")),  # sender, style, text
    ("A3b_C_c2s", ("r",), (b"C",)),
    ("A3b_C_s2c", ("r", "r"), (b"C", b"\x65")),
    (
        "A3b_c",  # shooter:num, grid, Xoff, Y, Zoff, orient, pitch, kind
        ("r", "num", "grid", "num", "num", "num", "num", "num", "b10"),
        (b"c", 0, (1, 1), 207, 212, 235, 3196, 0, 1),
    ),
    ("A3b_I_c2s", ("r", "num", "b10"), (b"I", 1, 0)),  # target num 20 -> 1, damage 0
]


def _vector_payload(name: str) -> bytes:
    if name in FULL_FRAMES:
        return parse_one(FULL_FRAMES[name][1]).payload
    return PAYLOADS[name]


@pytest.mark.parametrize(("name", "ops", "expected"), FIELD_CASES, ids=[c[0] for c in FIELD_CASES])
def test_spec_vector_fields(name: str, ops: _Ops, expected: tuple[object, ...]) -> None:
    r = Reader(_vector_payload(name))
    got: list[object] = []
    for op in ops:
        match op:
            case "r":
                got.append(r.raw(1))
            case "rest":
                got.append(r.rest())
            case _:
                got.append(getattr(r, op)())
    assert tuple(got) == expected
    assert r.at_end()


def test_every_vector_has_a_field_case() -> None:
    covered = {c[0] for c in FIELD_CASES}
    assert covered == set(FULL_FRAMES) | set(PAYLOADS)


def test_grenade_relay_is_the_client_payload_behind_a_tag() -> None:
    c2s = parse_one(FULL_FRAMES["A3b_E_grenade_c2s"][1]).payload
    s2c = parse_one(FULL_FRAMES["A3b_E_grenade_s2c"][1]).payload
    assert s2c == c2s[:1] + b"\x87" + c2s[1:]


def test_back_to_back_without_terminator() -> None:
    raw = FULL_FRAMES["A3_S"][1] + FULL_FRAMES["A3_b"][1]
    assert [f.type for f in FrameReader().feed(raw)] == ["S", "b"]


def test_build_frame_rejects_unframable_payloads() -> None:
    for bad in (b"", b"A" * (MAX_PAYLOAD + 1), b"A\x0a", b"A\x0d", b"A\x0e"):
        with pytest.raises(WireError):
            build_frame(bad)
    with pytest.raises(WireError):
        build_frame(b"A", terminator=b"\x0a")
    assert len(build_frame(b"A" * MAX_PAYLOAD, terminator=b"")) == 0xFF - 0x10
    assert build_frame(b"A", terminator=b"\x0e")[-1] == 0x0E


def test_leading_garbage_and_watchdog_pings_skipped() -> None:
    raw = FULL_FRAMES["A3_S"][1]
    reader = FrameReader()
    frames = reader.feed(b"\x05\x05AT&F\r" + raw + b"\x05")
    assert [f.raw for f in frames] == [raw]
    assert reader.skipped_bytes == 8
    assert reader.bad_frames == 0


def test_trailing_terminator_in_next_chunk_not_counted_as_noise() -> None:
    raw = FULL_FRAMES["A3_A"][1]
    reader = FrameReader()
    assert len(reader.feed(raw[:-1])) == 1
    assert reader.feed(raw[-1:]) == []
    assert reader.skipped_bytes == 0


def test_bad_payload_checksum_dropped_and_next_frame_kept() -> None:
    good = FULL_FRAMES["A3_S"][1]
    bad = bytearray(FULL_FRAMES["A3_b"][1])
    bad[8] ^= 0x04  # a payload byte: CK, ~CK and LEN stay valid, so the whole span is read
    reader = FrameReader()
    frames = reader.feed(bytes(bad) + good)
    assert [f.raw for f in frames] == [good]
    assert reader.bad_frames == 1
    assert reader.skipped_bytes == len(bad)


def test_frame_starting_inside_a_bad_frame_is_recovered() -> None:
    # Header valid, LEN long, payload checksum wrong, and a real frame inside the span: only
    # "drop the 'C' and rescan from the next byte" (not "skip LEN") finds it.
    inner = build_frame(b"S\x10", terminator=b"")
    outer = bytearray(build_frame(b"Y" * 40, terminator=b""))
    outer[10] ^= 0x01
    reader = FrameReader()
    frames = reader.feed(bytes(outer[:12]) + inner + bytes(outer[12:]))
    assert [f.raw for f in frames] == [inner]
    assert reader.bad_frames == 1


def test_invalid_len_counted() -> None:
    reader = FrameReader()
    assert reader.feed(b"CS\x20\xdf\x10") == []  # LEN 0x10: no payload
    assert reader.bad_frames == 1


def test_len_for_an_empty_payload_is_rejected() -> None:
    # LEN 0x15 = header only. CK for an empty payload is (0x15 & 0x7f) + 0x10 = 0x25.
    reader = FrameReader()
    assert reader.feed(b"CS\x25\xda\x15") == []
    assert reader.bad_frames == 1
    assert reader.skipped_bytes == 5


def test_ck_outside_checksum_range_rejected_at_once() -> None:
    # CK 0x90 can never be produced; the header is complementary and LEN claims 200 bytes. The
    # real frame behind it must not wait for the span.
    ping = build_frame(b"EN", terminator=b"")
    reader = FrameReader()
    frames = reader.feed(b"CS\x90\x6f\xd8" + ping)
    assert [f.raw for f in frames] == [ping]
    assert reader.bad_frames == 1


def test_client_lf_terminator_consumed_not_counted() -> None:
    raw = FULL_FRAMES["A3_S"][1]
    reader = FrameReader()
    assert [f.raw for f in reader.feed(raw + b"\x0a")] == [raw]
    assert reader.skipped_bytes == 0
    # And with the LF in the next chunk.
    reader = FrameReader()
    assert len(reader.feed(raw)) == 1
    assert reader.feed(b"\x0a") == []
    assert reader.skipped_bytes == 0


def test_skipped_bytes_account_for_everything_outside_good_frames() -> None:
    good = build_frame(b"S\x10", terminator=b"")
    bad = bytearray(build_frame(b"b" * 10, terminator=b""))
    bad[7] ^= 0x01
    noise = b"\x05\x05xyz"
    reader = FrameReader()
    frames = reader.feed(noise + bytes(bad) + good + b"\x0a" + b"\x05")
    assert len(frames) == 1
    # Skipped: the noise, the bad frame, the trailing 0x05. The one terminator is free.
    assert reader.skipped_bytes == len(noise) + len(bad) + 1
    assert reader.bad_frames == 1


def test_is_upper_bounds() -> None:
    def upper(letter: bytes) -> bool:
        return Frame(letter + b"\x10").is_upper

    assert upper(b"A") and upper(b"Z")
    assert not upper(b"@") and not upper(b"[") and not upper(b"a")


def _check_accounting(reader: FrameReader, fed: int) -> None:
    pending = len(reader._buf)
    assert pending <= MAX_PENDING  # the LEN bound: the buffer holds at most one partial frame
    # A fed byte is in a good frame, a terminator, skipped, or pending: never counted twice.
    assert reader.skipped_bytes + pending <= fed


def test_buffer_stays_bounded_by_len() -> None:
    reader = FrameReader()
    rng = random.Random(4)
    fed = 0
    for _ in range(200):
        chunk = bytes(rng.randrange(256) for _ in range(500))
        reader.feed(chunk)
        fed += len(chunk)
        _check_accounting(reader, fed)
    # A valid header claiming the largest span, then fewer bytes than it claims.
    reader = FrameReader()
    reader.feed(b"CS\x20\xdf\xff" + b"A" * 100)
    assert len(reader._buf) == 105
    reader.feed(b"A" * 200)
    assert len(reader._buf) <= MAX_PENDING


def test_fuzz_random_bytes_never_raise() -> None:
    rng = random.Random(5)
    reader = FrameReader()
    fed = 0
    for _ in range(3000):
        n = rng.randrange(0, 80)
        chunk = bytes(rng.choice([0x43, 0x53, 0x05, rng.randrange(256)]) for _ in range(n))
        reader.feed(chunk)
        fed += len(chunk)
        _check_accounting(reader, fed)


def test_fuzz_valid_frames_spliced_into_noise_come_out() -> None:
    rng = random.Random(6)
    for _ in range(300):
        payloads = [
            bytes([rng.randrange(0x41, 0x63)])
            + bytes(rng.randrange(0x10, 0x100) for _ in range(rng.randrange(0, 40)))
            for _ in range(rng.randrange(1, 5))
        ]
        stream = bytearray()
        for p in payloads:
            # Noise without "CS", so it cannot masquerade as a frame start.
            noise = bytes(
                rng.choice([0x05, 0x00, 0x41, 0x53, 0x20]) for _ in range(rng.randrange(8))
            )
            stream += noise + build_frame(p, terminator=rng.choice([b"", b"\x0d"]))
        reader = FrameReader()
        out: list[bytes] = []
        pos = 0
        while pos < len(stream):
            step = rng.randrange(1, 30)
            out += [f.payload for f in reader.feed(bytes(stream[pos : pos + step]))]
            pos += step
        assert out == payloads


def test_h_helper_ignores_spaces() -> None:
    assert h("43 53") == b"CS"


def test_frame_rejects_empty_payload() -> None:
    with pytest.raises(WireError):
        Frame(b"")
