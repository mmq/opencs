"""Spec-level byte builders for message tests, written from I §4.3 and I §8 conventions.

They are deliberately independent of the message classes and of ``Writer``: a test that builds its
expected bytes here fails if a message uses the wrong codec for a field, which a plain round trip
cannot show (both halves would share the mistake).
"""

from __future__ import annotations

from opencs.wire.codec import enc_num_encode


def b10(v: int) -> bytes:
    """``bias10``: one byte, value + 0x10 (I §4.3)."""
    return bytes([v + 0x10])


def b88(v: int) -> bytes:
    """``bias88``: one byte, value + 0x88 (I §4.3)."""
    return bytes([v + 0x88])


def num(v: int) -> bytes:
    """``enc_num`` (I §4.3), via the M1 codec whose worked values are tested there."""
    return enc_num_encode(v)


def string(s: bytes) -> bytes:
    """``str``: a ``bias10`` length then the bytes (I §4.3)."""
    return b10(len(s)) + s


def grid(cell_x: int, cell_z: int) -> bytes:
    """The raw grid byte ``((x << 4) | z) + 0x10`` (SPEC A conventions)."""
    return bytes([((cell_x << 4) | cell_z) + 0x10])
