"""The three number codecs, strings and grid bytes (I §4.3, I §6.1), plus cursor classes.

Three different codecs are in use; each is its own function pair. ``Reader`` and ``Writer`` let a
message class read like its Appendix A layout. Decoding raises ``WireError`` and nothing else.
"""

from __future__ import annotations

from opencs.wire.errors import WireError
from opencs.wire.frame import TERMINATORS

#: Offset added to every bias10 and enc_num byte; keeps them above every terminator
#: (I §4.3, I §4.3).
BIAS10 = 0x10

#: Offset added to a bias88 byte (I §4.3).
BIAS88 = 0x88

#: Smallest and largest byte the encoders may emit: below 0x10 would collide with the frame
#: terminators 0x0a/0x0d/0x0e (I §4.1).
BYTE_MIN = 0x10
BYTE_MAX = 0xFF

#: bias10 value range: ``0 .. 0xef``.
BIAS10_MIN = BYTE_MIN - BIAS10
BIAS10_MAX = BYTE_MAX - BIAS10

#: bias88 value range that keeps the wire byte within ``0x10..0xff``: ``-0x78 .. 0x77``
#: (I §4.1, I §4.3). The server sends nothing outside it.
BIAS88_MIN = BYTE_MIN - BIAS88
BIAS88_MAX = BYTE_MAX - BIAS88

#: The full bias88 range, ``-0x88 .. 0x77``: a client may send values down to ``-0x88``, whose
#: bytes fall below 0x10 (I §4.3).
BIAS88_WIRE_MIN = -BIAS88

#: bias88 values whose byte is a frame terminator: -126, -123 and -122 (I §4.1, I §4.3).
BIAS88_FORBIDDEN = frozenset(b - BIAS88 for b in TERMINATORS)

#: Longest string: its bias10 length byte must stay a legal byte (I §4.3).
MAX_STRING_LENGTH = BIAS10_MAX

#: enc_num: low part is ``v % 15``, higher part is base-240 digits, at most 7 of them (I §4.3).
ENC_NUM_LOW_BASE = 15
ENC_NUM_DIGIT_BASE = 240
ENC_NUM_MAX_DIGITS = 7

#: Largest magnitude enc_num can carry.
ENC_NUM_MAX = ENC_NUM_LOW_BASE * ENC_NUM_DIGIT_BASE**ENC_NUM_MAX_DIGITS - 1

#: Grid byte nibbles: the byte must stay <= 0xff, so cell x is 0..14 and cell z 0..15 (I §6.1).
GRID_X_MAX = 14
GRID_Z_MAX = 15

#: The city is 8x8 cells of 600 feet (I §6.1).
CELL_SIZE = 600
CITY_CELLS = 8
CITY_SIZE = CELL_SIZE * CITY_CELLS


def bias10_encode(v: int) -> int:
    """Wire byte for a small scalar (I §4.3)."""
    b = v + BIAS10
    if not BYTE_MIN <= b <= BYTE_MAX:
        raise WireError(f"bias10 value {v} outside {BIAS10_MIN}..{BIAS10_MAX}")
    return b


def bias10_decode(b: int) -> int:
    """Value of a bias10 byte (I §4.3)."""
    return b - BIAS10


def bias88_encode(v: int) -> int:
    """Wire byte for a signed motion scalar (I §4.3); range ``BIAS88_WIRE_MIN..BIAS88_MAX`` less
    ``BIAS88_FORBIDDEN``."""
    if not BIAS88_WIRE_MIN <= v <= BIAS88_MAX:
        raise WireError(f"bias88 value {v} outside {BIAS88_WIRE_MIN}..{BIAS88_MAX}")
    if v in BIAS88_FORBIDDEN:
        raise WireError(f"bias88 value {v} encodes as a frame terminator")
    return v + BIAS88


def bias88_decode(b: int) -> int:
    """Value of a bias88 byte; any byte is accepted (the client's frames are parsed by length)."""
    return b - BIAS88


def enc_num_encode(v: int) -> bytes:
    """Variable-length integer (I §4.3)."""
    neg = 1 if v < 0 else 0
    v = abs(v)
    q, n0 = divmod(v, ENC_NUM_LOW_BASE)
    digits: list[int] = []
    while q:
        q, d = divmod(q, ENC_NUM_DIGIT_BASE)
        digits.append(d)
    if len(digits) > ENC_NUM_MAX_DIGITS:
        raise WireError(f"enc_num value too large: {v}")
    first = ((n0 << 4) | (len(digits) << 1) | neg) + BIAS10
    return bytes([first, *(d + BIAS10 for d in digits)])


def enc_num_decode(buf: bytes, pos: int = 0) -> tuple[int, int]:
    """Decode an enc_num at ``pos``; return ``(value, new_pos)`` (I §4.3)."""
    if pos < 0 or pos >= len(buf):
        raise WireError("enc_num: truncated")
    x = buf[pos] - BIAS10
    if x < 0:
        raise WireError("enc_num: byte below 0x10")
    neg = x & 1
    count = (x >> 1) & 7
    value = x >> 4
    end = pos + 1 + count
    if end > len(buf):
        raise WireError("enc_num: truncated")
    mult = ENC_NUM_LOW_BASE
    for d in buf[pos + 1 : end]:
        if d < BIAS10:
            raise WireError("enc_num: digit below 0x10")
        value += (d - BIAS10) * mult
        mult *= ENC_NUM_DIGIT_BASE
    return (-value if neg else value), end


def str_encode(s: bytes) -> bytes:
    """A string: bias10 length then the bytes (I §4.3)."""
    if len(s) > MAX_STRING_LENGTH:
        raise WireError(f"string too long: {len(s)}")
    return bytes([bias10_encode(len(s))]) + s


def str_decode(buf: bytes, pos: int = 0) -> tuple[bytes, int]:
    """Decode a string at ``pos``; return ``(bytes, new_pos)`` (I §4.3)."""
    if pos < 0 or pos >= len(buf):
        raise WireError("string: truncated")
    n = buf[pos] - BIAS10
    if n < 0:
        raise WireError("string: length byte below 0x10")
    start = pos + 1
    if start + n > len(buf):
        raise WireError("string: truncated")
    return bytes(buf[start : start + n]), start + n


def grid_encode(cell_x: int, cell_z: int) -> int:
    """The one-byte cell index ``((x << 4) | z) + 0x10`` (I §6.1)."""
    if not (0 <= cell_x <= GRID_X_MAX and 0 <= cell_z <= GRID_Z_MAX):
        raise WireError(f"grid cell ({cell_x}, {cell_z}) not encodable")
    return ((cell_x << 4) | cell_z) + BIAS10


def grid_decode(b: int) -> tuple[int, int]:
    """Cell ``(x, z)`` of a grid byte (I §6.1)."""
    if not BYTE_MIN <= b <= BYTE_MAX:
        raise WireError(f"grid byte {b:#x} below 0x10")
    v = b - BIAS10
    return v >> 4, v & 0x0F


def _split_axis(v: int) -> tuple[int, int]:
    """Cell and offset for one axis. Off-map values stay in the edge cell with a signed or
    oversized offset, never wrapping to another cell (I §6.1; the high side is our choice beyond
    4800 ft)."""
    if v < 0:
        cell = 0
    elif v >= CITY_SIZE:
        cell = CITY_CELLS - 1
    else:
        cell = v // CELL_SIZE
    return cell, v - cell * CELL_SIZE


def split_position(world_x: int, world_z: int) -> tuple[int, int, int, int]:
    """World feet to ``(cell_x, x_off, cell_z, z_off)`` (I §6.1)."""
    cx, xo = _split_axis(world_x)
    cz, zo = _split_axis(world_z)
    return cx, xo, cz, zo


def join_position(cell_x: int, x_off: int, cell_z: int, z_off: int) -> tuple[int, int]:
    """Inverse of ``split_position``: world feet ``(x, z)``, for any decoded cell (I §6.1)."""
    return cell_x * CELL_SIZE + x_off, cell_z * CELL_SIZE + z_off


class Reader:
    """A cursor over a payload. Every method raises ``WireError`` on truncation, nothing else."""

    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes, pos: int = 0) -> None:
        if not 0 <= pos <= len(buf):
            raise WireError("start position outside the buffer")
        self.buf = bytes(buf)
        self.pos = pos

    def _byte(self) -> int:
        if self.pos >= len(self.buf):
            raise WireError("truncated")
        b = self.buf[self.pos]
        self.pos += 1
        return b

    def b10(self) -> int:
        """A bias10 scalar."""
        return bias10_decode(self._byte())

    def b88(self) -> int:
        """A bias88 scalar."""
        return bias88_decode(self._byte())

    def num(self) -> int:
        """An enc_num."""
        value, self.pos = enc_num_decode(self.buf, self.pos)
        return value

    def str(self) -> bytes:
        """A length-prefixed string."""
        value, self.pos = str_decode(self.buf, self.pos)
        return value

    def grid(self) -> tuple[int, int]:
        """A grid byte as ``(cell_x, cell_z)``."""
        return grid_decode(self._byte())

    def raw(self, n: int) -> bytes:
        """Exactly ``n`` raw bytes."""
        if n < 0 or self.pos + n > len(self.buf):
            raise WireError("truncated")
        out = self.buf[self.pos : self.pos + n]
        self.pos += n
        return out

    def rest(self) -> bytes:
        """Everything left."""
        out = self.buf[self.pos :]
        self.pos = len(self.buf)
        return out

    def at_end(self) -> bool:
        """True when every byte has been consumed."""
        return self.pos >= len(self.buf)


class Writer:
    """Builds a payload with the same vocabulary as ``Reader``."""

    __slots__ = ("_parts",)

    def __init__(self) -> None:
        self._parts: list[bytes] = []

    def b10(self, v: int) -> Writer:
        """Append a bias10 scalar."""
        self._parts.append(bytes([bias10_encode(v)]))
        return self

    def b88(self, v: int) -> Writer:
        """Append a bias88 scalar."""
        self._parts.append(bytes([bias88_encode(v)]))
        return self

    def num(self, v: int) -> Writer:
        """Append an enc_num."""
        self._parts.append(enc_num_encode(v))
        return self

    def str(self, s: bytes) -> Writer:
        """Append a length-prefixed string."""
        self._parts.append(str_encode(s))
        return self

    def grid(self, cell_x: int, cell_z: int) -> Writer:
        """Append a grid byte."""
        self._parts.append(bytes([grid_encode(cell_x, cell_z)]))
        return self

    def raw(self, data: bytes) -> Writer:
        """Append bytes as they are (a type letter, a tag, a literal)."""
        self._parts.append(bytes(data))
        return self

    def bytes(self) -> bytes:
        """The payload built so far."""
        return b"".join(self._parts)
