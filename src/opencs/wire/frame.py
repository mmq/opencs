"""Frame build and an incremental, sans-IO frame reader (I §4.1).

``'C' 'S' CK ~CK LEN payload [TERM]``. Frames are delimited by LEN, never by terminator. The
reader never raises: hostile bytes are skipped and counted (DESIGN §5).
"""

from __future__ import annotations

from dataclasses import dataclass

from opencs.wire.errors import WireError

#: Frame magic (I §4.1).
MAGIC = b"CS"

#: Bytes before the payload: 'C' 'S' CK ~CK LEN (I §4.1).
HEADER_SIZE = 5

#: Added to ``HEADER_SIZE + len(payload)`` to form LEN, and to the checksum (I §4.1).
LEN_BIAS = 0x10

#: Largest payload: ``5 + 234 + 0x10 = 0xff`` (I §4.1).
MAX_PAYLOAD = 0xFF - LEN_BIAS - HEADER_SIZE

#: Trailing terminators (I §4.1): CR or SO to the client, LF from it.
TERM_LF = 0x0A
TERM_CR = 0x0D
TERM_SO = 0x0E
TERMINATORS = frozenset({TERM_LF, TERM_CR, TERM_SO})

#: Smallest legal LEN: a one-byte payload.
_MIN_LEN = LEN_BIAS + HEADER_SIZE + 1

#: CK is ``(x & 0x7f) + 0x10`` by construction, so only these values can be a checksum (I §4.1).
_CK_MIN = 0x10
_CK_MAX = 0x8F

#: The most a reader holds back: a frame one byte short of its LEN, ``0xff - 0x10 - 1`` (I §4.1).
MAX_PENDING = 0xFF - LEN_BIAS - 1


def checksum(length_byte: int, payload: bytes) -> int:
    """CK for a frame with this LEN byte and payload (I §4.1)."""
    return ((length_byte + sum(payload)) & 0x7F) + 0x10


def build_frame(payload: bytes, *, terminator: bytes = b"\x0d") -> bytes:
    """Wrap ``payload`` in a frame (I §4.1). Raises ``WireError`` for what no legal frame holds."""
    if not payload:
        raise WireError("empty payload")
    if len(payload) > MAX_PAYLOAD:
        raise WireError(f"payload {len(payload)} bytes exceeds {MAX_PAYLOAD}")
    if any(b in TERMINATORS for b in payload):
        raise WireError("payload contains 0x0a, 0x0d or 0x0e")
    if terminator not in (b"", b"\x0d", b"\x0e"):
        raise WireError("terminator must be 0x0d, 0x0e or empty")
    length = HEADER_SIZE + len(payload) + LEN_BIAS
    ck = checksum(length, payload)
    return MAGIC + bytes([ck, ck ^ 0xFF, length]) + payload + terminator


@dataclass(frozen=True, slots=True)
class Frame:
    """One parsed frame: its payload, and the bytes it came from (for logs and capture)."""

    payload: bytes
    raw: bytes = b""

    def __post_init__(self) -> None:
        if not self.payload:
            raise WireError("a frame has at least a type byte")

    @property
    def type(self) -> str:
        """The message type letter (I §4.1)."""
        return chr(self.payload[0])

    @property
    def is_upper(self) -> bool:
        """True for an uppercase type, which carries a tag byte (I §4.2)."""
        return 0x41 <= self.payload[0] <= 0x5A


class FrameReader:
    """Incremental frame parser. ``feed`` takes any chunking and returns the frames completed.

    Frames are delimited by LEN (I §4.1), so the buffer never holds more than ``MAX_PENDING``
    bytes. The cost of that: a stray ``CS`` followed by a header that passes the CK, ~CK and LEN
    checks makes the reader wait for the span LEN claims before the payload checksum rejects it,
    and real frames inside the span are held back until then (at most 238 bytes). A CK outside
    ``0x10..0x8f`` is rejected at once, which halves the odds; ASCII text can never form a valid
    header (~CK of an ASCII byte is 0x80 or more). A caller that needs bounded latency for a
    pending partial frame must time it out itself (M3).
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._skip_term = False
        self.frames = 0
        self.bad_frames = 0
        self.skipped_bytes = 0

    def feed(self, data: bytes) -> list[Frame]:
        """Add bytes; return every frame now complete. Never raises."""
        self._buf += data
        buf = self._buf
        out: list[Frame] = []
        pos = 0
        n = len(buf)
        while pos < n:
            if self._skip_term:
                self._skip_term = False
                if buf[pos] in TERMINATORS:
                    pos += 1
                    continue
            start = buf.find(MAGIC, pos)
            if start < 0:
                # Keep a final 'C': its 'S' may be in the next chunk.
                keep = n - 1 if buf[n - 1] == MAGIC[0] else n
                self.skipped_bytes += keep - pos
                pos = keep
                break
            self.skipped_bytes += start - pos
            pos = start
            if n - pos < HEADER_SIZE:
                break
            ck, nck, length = buf[pos + 2], buf[pos + 3], buf[pos + 4]
            total = length - LEN_BIAS
            if nck != ck ^ 0xFF or not _CK_MIN <= ck <= _CK_MAX or length < _MIN_LEN:
                self._reject()
                pos += 1
                continue
            if n - pos < total:
                break
            payload = bytes(buf[pos + HEADER_SIZE : pos + total])
            if checksum(length, payload) != ck:
                self._reject()
                pos += 1
                continue
            out.append(Frame(payload, bytes(buf[pos : pos + total])))
            self.frames += 1
            pos += total
            if pos < n:
                if buf[pos] in TERMINATORS:
                    pos += 1
            else:
                self._skip_term = True
        del buf[:pos]
        return out

    def _reject(self) -> None:
        self.bad_frames += 1
        self.skipped_bytes += 1
