"""Chamber text helpers (I §5.1, I §5.2, I §5.5, II §4)."""

from __future__ import annotations

import random

import pytest

from opencs import policy
from opencs.wire.errors import WireError
from opencs.wire.text import (
    HHHScanner,
    LineReader,
    QuitScanner,
    arm_line,
    arm_plus,
    bell,
    find_hhh,
    plain_line,
    sanitize_relay,
)


def test_lines_split_on_cr_across_chunks() -> None:
    r = LineReader()
    assert r.feed(b"/PL") == []
    assert r.feed(b"AY\r/WHO") == [b"/PLAY"]
    assert r.feed(b" X\r") == [b"/WHO X"]


def test_empty_lines_preserved_and_lf_ignored() -> None:
    r = LineReader()
    assert r.feed(b"\r\r\nNAME,PW\r\n") == [b"", b"", b"NAME,PW"]


def test_control_bytes_dropped() -> None:
    r = LineReader()
    assert r.feed(b"AB\x1bC\x18D\x00E\x7f\r") == [b"ABCDE"]


def test_overlong_line_discarded_and_counted_then_recovers() -> None:
    r = LineReader()
    assert r.feed(b"x" * (policy.MAX_LINE_BYTES * 3)) == []
    assert r.overlong_lines == 1
    assert r.feed(b"yyy\rok\r") == [b"ok"]
    assert r.overlong_lines == 1
    assert r.feed(b"z" * policy.MAX_LINE_BYTES + b"\r") == [b"z" * policy.MAX_LINE_BYTES]


def test_hhh_after_dial_noise_and_in_split_feed() -> None:
    assert find_hhh(b"ATZ\rATDT1\rHHH") == 10
    assert find_hhh(b"ATZ\r") is None
    s = HHHScanner()
    assert s.feed(b"ATDT\rH") is None
    assert s.feed(b"H") is None
    assert s.feed(b"Hrest") == b"rest"
    assert s.found
    assert s.feed(b"more") == b"more"
    t = HHHScanner()
    assert t.feed(b"AT\rHHH") == b""


def test_quit_amid_binary_and_split() -> None:
    q = QuitScanner()
    assert q.feed(b"CS\x10\x10\x16AQUIT\rCS") == 1
    assert q.feed(b"QU") == 0
    assert q.feed(b"IT") == 0
    assert q.feed(b"\r") == 1
    assert q.feed(b"QUIT\rQUIT\r") == 2
    r = QuitScanner()
    assert r.feed(b"QUIT\r") == 1
    assert r.feed(b"\r") == 0  # the first match's bytes are not reused


def test_arm_helpers() -> None:
    assert arm_line("A") == b"\x05A\r"
    assert arm_line("E 1 1") == b"\x05E 1 1\r"
    assert arm_plus() == b"\x01"
    assert bell() == b"\x07"


def test_arm_line_guards() -> None:
    assert len(arm_line("a" * 510)) == 512  # text + CR is 511 bytes
    with pytest.raises(WireError):
        arm_line("a" * 511)
    with pytest.raises(WireError):
        arm_line("A\x05B")
    with pytest.raises(WireError):
        arm_line("A\r")


def test_plain_line_and_sanitize() -> None:
    assert plain_line("Hi there") == b"Hi there\r"
    assert plain_line("a\x01b\x05c\x07d\n\x1beé中~") == b"abcde~\r"
    assert sanitize_relay(b"a\x01\x05\x7f\xffb\r\n") == b"ab"


def test_fuzz_text_helpers_never_raise() -> None:
    rng = random.Random(7)
    lines, hhh, quit_ = LineReader(), HHHScanner(), QuitScanner()
    for _ in range(3000):
        chunk = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 60)))
        lines.feed(chunk)
        hhh.feed(chunk)
        quit_.feed(chunk)
        sanitize_relay(chunk)
