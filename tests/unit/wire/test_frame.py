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
