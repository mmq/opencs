"""Codecs against the spec's worked values (I §4.3, I §6.1, I App. A.1)."""

from __future__ import annotations

import random

import pytest

from opencs.wire.codec import (
    BIAS88_MAX,
    BIAS88_MIN,
    ENC_NUM_MAX,
    Reader,
    Writer,
    bias10_decode,
    bias10_encode,
    bias88_decode,
    bias88_encode,
    enc_num_decode,
    enc_num_encode,
    grid_decode,
    grid_encode,
    join_position,
    split_position,
    str_decode,
    str_encode,
)
from opencs.wire.errors import WireError


def test_bias10() -> None:
    assert bias10_encode(0) == 0x10
    assert bias10_decode(0x18) == 8
    assert bias10_encode(0xEF) == 0xFF
    for bad in (-1, 0xF0):
        with pytest.raises(WireError):
            bias10_encode(bad)


def test_bias88_bounds_keep_clear_of_terminators() -> None:
    assert (BIAS88_MIN, BIAS88_MAX) == (-0x78, 0x77)
    assert bias88_encode(0) == 0x88
    assert bias88_encode(BIAS88_MIN) == 0x10
    assert bias88_encode(BIAS88_MAX) == 0xFF
    assert bias88_decode(0x88) == 0
    assert bias88_decode(0x00) == -0x88  # decoding accepts any byte
    for bad in (BIAS88_MIN - 1, BIAS88_MAX + 1):
        with pytest.raises(WireError):
            bias88_encode(bad)


@pytest.mark.parametrize(
    ("value", "wire"),
    [
        (145, "b219"),
        (240, "1220"),
        (4096, "243111"),
        (3000, "12d8"),
        (450, "122e"),
        (0, "10"),
        (-1, "21"),
        (5, "60"),
    ],
)
def test_enc_num_worked_values(value: int, wire: str) -> None:
    assert enc_num_encode(value) == bytes.fromhex(wire)
    assert enc_num_decode(bytes.fromhex(wire), 0) == (value, len(wire) // 2)


def test_enc_num_round_trip_range() -> None:
    values = {0, 1, 14, 15, 3599, 32767, 65535, 10**9, ENC_NUM_MAX}
    values |= {random.Random(1).randrange(ENC_NUM_MAX) for _ in range(500)}
    for v in values:
        for s in (v, -v):
            enc = enc_num_encode(s)
            assert min(enc) >= 0x10
            assert enc_num_decode(b"xx" + enc + b"yy", 2) == (s, 2 + len(enc))


def test_enc_num_too_large() -> None:
    with pytest.raises(WireError):
        enc_num_encode(ENC_NUM_MAX + 1)
    assert len(enc_num_encode(ENC_NUM_MAX)) == 8


def test_enc_num_decode_errors_are_wire_errors() -> None:
    for buf, pos in [(b"", 0), (b"\x12", 0), (b"\x05", 0), (b"\x12\x05", 0), (b"\x10", 1)]:
        with pytest.raises(WireError):
            enc_num_decode(buf, pos)
    with pytest.raises(WireError):
        enc_num_decode(b"\x10", -1)
    # A byte below 0x10 that is not truncated: only the low-byte rule can reject it.
    with pytest.raises(WireError, match="below 0x10"):
        enc_num_decode(b"\x0f\x10\x10\x10\x10\x10\x10\x10")


def test_str_decode_rejects_negative_position() -> None:
    # Python's buf[-1] would read the last byte (a zero length) if the guard were missing.
    with pytest.raises(WireError):
        str_decode(b"\x12\x10", -1)


def test_reader_rejects_bad_start_and_negative_raw() -> None:
    for pos in (-1, 3):
        with pytest.raises(WireError):
            Reader(b"ab", pos)
    assert Reader(b"ab", 2).at_end()
    r = Reader(b"ab", 1)
    with pytest.raises(WireError):
        r.raw(-1)
    assert r.pos == 1


def test_str() -> None:
    assert str_encode(b"ok") == b"\x12ok"
    assert str_decode(b"\x12okZ", 0) == (b"ok", 3)
    assert str_decode(str_encode(b""), 0) == (b"", 1)
    assert len(str_encode(b"a" * 0xEF)) == 0xF0
    with pytest.raises(WireError):
        str_encode(b"a" * 0xF0)
    for buf in (b"", b"\x13a", b"\x05abc"):
        with pytest.raises(WireError):
            str_decode(buf, 0)


def test_grid() -> None:
    assert grid_encode(0, 0) == 0x10
    assert grid_encode(2, 2) == 0x32
    assert grid_decode(0x32) == (2, 2)
    assert grid_encode(14, 15) == 0xFF
    assert grid_decode(0xFF) == (14, 15)
    for x, z in [(15, 0), (0, 16), (-1, 0), (0, -1)]:
        with pytest.raises(WireError):
            grid_encode(x, z)
    with pytest.raises(WireError):
        grid_decode(0x0F)


def test_split_position() -> None:
    assert split_position(145, 5) == (0, 145, 0, 5)
    assert split_position(600, 1199) == (1, 0, 1, 599)
    assert split_position(-938, -1506) == (0, -938, 0, -1506)  # never cell -1
    assert split_position(4799, 0) == (7, 599, 0, 0)
    assert split_position(4800, 5000) == (7, 600, 7, 800)  # our choice beyond 4800 ft (I §6.1)


def test_position_round_trip() -> None:
    rng = random.Random(2)
    for _ in range(1000):
        x, z = rng.randrange(-3000, 8000), rng.randrange(-3000, 8000)
        assert join_position(*split_position(x, z)) == (x, z)


def test_reader_writer_mirror_a3_state() -> None:
    payload = bytes.fromhex(
        "62 10 b2 19 12 20 60 12 2e 88 88 10 10 14 12 d8 10 10 10".replace(" ", "")
    )
    r = Reader(payload)
    assert r.raw(1) == b"b"
    assert r.b10() == 0
    assert r.num() == 145
    assert r.num() == 240
    assert r.num() == 5
    assert r.num() == 450
    # A.2 'b': fwd:b88, fwd_target:b88, v1:num, v2:num, state:b10, power:num, weight:b10,
    # armor0:num, armor1:num. v1, v2 and armor are num, not b10 (I §4.3: wrong codec = desync).
    assert (r.b88(), r.b88(), r.num(), r.num(), r.b10()) == (0, 0, 0, 0, 4)
    assert r.num() == 3000
    assert not r.at_end()
    assert (r.b10(), r.num(), r.num()) == (0, 0, 0)  # weight, armor0, armor1
    assert r.at_end()
    w = (
        Writer()
        .raw(b"b")
        .b10(0)
        .num(145)
        .num(240)
        .num(5)
        .num(450)
        .b88(0)
        .b88(0)
        .num(0)
        .num(0)
        .b10(4)
        .num(3000)
        .b10(0)
        .num(0)
        .num(0)
    )
    assert w.bytes() == payload


def test_reader_truncation_only_raises_wire_error() -> None:
    r = Reader(b"")
    for call in (r.b10, r.b88, r.num, r.str, r.grid):
        with pytest.raises(WireError):
            call()
    with pytest.raises(WireError):
        r.raw(1)
    assert r.rest() == b""
    with pytest.raises(WireError):
        Reader(b"\x11").raw(2)
    with pytest.raises(WireError):
        Reader(b"\x05").grid()


def test_fuzz_decoders_never_raise_other_than_wire_error() -> None:
    rng = random.Random(3)
    for _ in range(3000):
        buf = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 12)))
        r = Reader(buf)
        for _ in range(6):
            try:
                rng.choice([r.b10, r.b88, r.num, r.str, r.grid, r.rest])()
                r.raw(rng.randrange(0, 4))
            except WireError:
                break
