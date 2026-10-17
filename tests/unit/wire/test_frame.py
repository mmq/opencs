"""Frame build/parse against the spec's real captures (I §4.1, I App. A.1, I App. A.2) and hostile
input."""

from __future__ import annotations

import random

import pytest

from fixtures.spec_vectors import FULL_FRAMES, PAYLOADS, h
from opencs import policy
from opencs.wire.codec import Reader
from opencs.wire.errors import WireError
from opencs.wire.frame import MAX_PAYLOAD, Frame, FrameReader, build_frame


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


def test_a3_state_message_fields() -> None:
    r = Reader(parse_one(FULL_FRAMES["A3_b"][1]).payload)
    assert r.raw(1) == b"b"
    assert r.b10() == 0
    assert r.num() == 145  # Xoff
    assert r.num() == 240  # Y
    assert r.num() == 5  # Zoff
    assert r.num() == 450  # orient
    assert (r.b88(), r.b88(), r.b10(), r.b10()) == (0, 0, 0, 0)  # fwd, fwd_target, v1, v2
    assert r.b10() == 4  # state: drop
    assert r.num() == 3000  # power
    assert r.b10() == 0  # weight
    assert (r.b10(), r.b10()) == (0, 0)  # armor
    assert r.at_end()


def test_a3_identity_message_fields() -> None:
    r = Reader(parse_one(FULL_FRAMES["A3_B"][1]).payload)
    assert (r.raw(1), r.raw(1)) == (b"B", b"\x11")  # type, tag
    assert (r.b10(), r.b10()) == (0, 0)  # self_id, slot
    assert r.str() == b"JOINGENI"
    assert r.str() == b"GENIE"
    assert r.str() == b"CYBERCITY ONE"
    assert (r.b10(), r.b10(), r.b10(), r.b10(), r.b10()) == (8, 1, 2, 0, 0)
    assert r.at_end()


def test_a3b_fields() -> None:
    g = Reader(parse_one(FULL_FRAMES["A3b_G"][1]).payload)
    g.raw(2)  # type, tag
    assert g.b10() == 1  # id: a pod id is a b10 byte (I §6.2)
    assert g.str() == b"JOINGEN2"
    assert g.b10() == 8  # slot: index 1, team 0
    assert g.grid() == (0, 0)
    assert (g.num(), g.num(), g.num(), g.num()) == (75, 240, 75, 450)
    assert (g.b88(), g.b10(), g.b10()) == (0, 0, 0)
    assert g.at_end()

    e = Reader(parse_one(FULL_FRAMES["A3b_E_grenade_c2s"][1]).payload)
    assert e.raw(2) == b"EG"
    assert e.grid() == (2, 2)
    assert (e.num(), e.num(), e.num(), e.num(), e.num()) == (100, 72, 300, 869, 3332)
    assert e.at_end()

    i = Reader(parse_one(FULL_FRAMES["A3_I"][1]).payload)
    assert i.raw(1) == b"I"
    assert i.num() == 4096
    assert i.b10() == 0

    u = Reader(parse_one(FULL_FRAMES["A3b_U"][1]).payload)
    assert (u.raw(1), u.b10(), u.str()) == (b"U", 0, b"ok")

    c = Reader(PAYLOADS["A3b_c"])
    assert (c.raw(1), c.b10()) == (b"c", 0)
    assert c.grid() == (1, 1)
    assert (c.num(), c.num(), c.num(), c.num(), c.num()) == (207, 212, 235, 3196, 0)
    assert c.b10() == 1


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


def test_corrupt_checksum_dropped_and_next_frame_kept() -> None:
    good = FULL_FRAMES["A3_S"][1]
    bad = bytearray(FULL_FRAMES["A3_b"][1])
    bad[2] ^= 0x01
    reader = FrameReader()
    frames = reader.feed(bytes(bad) + good)
    assert [f.raw for f in frames] == [good]
    assert reader.bad_frames == 1


def test_corrupt_payload_byte_dropped() -> None:
    bad = bytearray(FULL_FRAMES["A3_b"][1])
    bad[8] ^= 0x04
    reader = FrameReader()
    assert reader.feed(bytes(bad)) == []
    assert reader.bad_frames >= 1


def test_frame_starting_inside_a_bad_frame_is_recovered() -> None:
    inner = FULL_FRAMES["A3_S"][1]
    # A header claiming a long frame, whose span swallows a real frame.
    liar = bytearray(build_frame(b"X" * 40))
    liar[3] ^= 0xFF  # break ~CK
    reader = FrameReader()
    frames = reader.feed(bytes(liar[:10]) + inner + b"tail")
    assert [f.raw for f in frames] == [inner]
    # Also with a good header/ck for the outer frame but a bad payload checksum.
    outer = bytearray(build_frame(b"Y" * 40))
    outer[2] ^= 0x02
    frames = FrameReader().feed(bytes(outer[:12]) + inner + bytes(outer[12:]))
    assert inner in [f.raw for f in frames]


def test_invalid_len_counted() -> None:
    reader = FrameReader()
    assert reader.feed(b"CS\x20\xdf\x10") == []  # LEN 0x10: no payload
    assert reader.bad_frames == 1


def test_buffer_stays_bounded() -> None:
    reader = FrameReader()
    rng = random.Random(4)
    for _ in range(200):
        reader.feed(bytes(rng.randrange(256) for _ in range(500)))
        assert len(reader._buf) <= policy.MAX_FRAME_BUFFER
    reader.feed(b"CS" + b"\x10\xef\xff" + b"A" * 100)
    assert len(reader._buf) <= policy.MAX_FRAME_BUFFER


def test_fuzz_random_bytes_never_raise() -> None:
    rng = random.Random(5)
    reader = FrameReader()
    for _ in range(3000):
        n = rng.randrange(0, 80)
        chunk = bytes(rng.choice([0x43, 0x53, 0x05, rng.randrange(256)]) for _ in range(n))
        reader.feed(chunk)
    assert reader.skipped_bytes + reader.bad_frames >= 0


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
