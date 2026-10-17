"""The Chamber's text side: inbound line reader, login and quit scanners, outbound helpers.

I §5.1 (login), I §5.2 (terminal, command mode), I §5.5 (quit), II §4.3 (relay hygiene).
"""

from __future__ import annotations

from opencs import policy
from opencs.wire.errors import WireError

CR = 0x0D
LF = 0x0A

#: Raw arming bytes (I §5.2): ``0x01`` arms and pre-seeds ``+``; ``0x05`` arms one line.
ARM_PLUS = b"\x01"
ARM_LINE = b"\x05"

#: BEL, the only control byte a plain line may carry, and only via ``bell()`` (I §5.2).
BEL = b"\x07"

#: An armed line must be shorter than this many bytes (I §5.2: the client copies it unchecked).
ARMED_LINE_LIMIT = 512

#: The login marker: the stream's first bytes, with no terminator (I §5.1).
HHH = b"HHH"

#: What a client sends to leave a game (I §5.5 step 4).
QUIT = b"QUIT\r"

_PRINTABLE_LOW = 0x20
_PRINTABLE_HIGH = 0x7E


class LineReader:
    """Splits an inbound byte stream into CR-terminated lines (I §5.3).

    LF is ignored, as are ESC, Ctrl-X and every other control byte, and bytes 0x80..0xff (the
    terminal is 7-bit, I §5.2), so a line is printable ASCII by construction. A line longer than
    ``policy.MAX_LINE_BYTES`` is discarded whole and counted, so memory stays bounded.
    Empty lines are returned; the login flow skips them (I §5.1).
    """

    def __init__(self) -> None:
        self._line = bytearray()
        self._overflow = False
        self.overlong_lines = 0

    def feed(self, data: bytes) -> list[bytes]:
        """Add bytes; return the lines completed, without terminators."""
        out: list[bytes] = []
        for b in data:
            if b == CR:
                if self._overflow:
                    self._overflow = False
                else:
                    out.append(bytes(self._line))
                self._line.clear()
            elif not _PRINTABLE_LOW <= b <= _PRINTABLE_HIGH:
                continue
            elif not self._overflow:
                if len(self._line) >= policy.MAX_LINE_BYTES:
                    self._overflow = True
                    self.overlong_lines += 1
                    self._line.clear()
                else:
                    self._line.append(b)
        return out


def find_hhh(buffer: bytes) -> int | None:
    """Index of the first ``HHH`` in ``buffer``, or None (I §5.1)."""
    i = buffer.find(HHH)
    return None if i < 0 else i


class HHHScanner:
    """Finds ``HHH`` in a stream that may open with modem-dial noise, across chunk boundaries."""

    def __init__(self) -> None:
        self._tail = b""
        self.found = False

    def feed(self, data: bytes) -> bytes | None:
        """Return the bytes after ``HHH`` once seen (``b""`` if none yet follow), else None.

        After the marker is found, every later chunk is returned unchanged.
        """
        if self.found:
            return data
        buf = self._tail + data
        i = find_hhh(buf)
        if i is None:
            self._tail = buf[-(len(HHH) - 1) :]
            return None
        self.found = True
        self._tail = b""
        return buf[i + len(HHH) :]


class QuitScanner:
    """Finds raw ``QUIT\\r`` in a stream that may carry binary frame tails (I §5.5 step 4),
    across chunk boundaries. Occurrences do not overlap."""

    def __init__(self) -> None:
        self._tail = b""

    def feed(self, data: bytes) -> list[int]:
        """Return, for each ``QUIT\\r`` this chunk completed, the offset in ``data`` just after it.

        The session passes ``data[offset:]`` to the Chamber once the second one arrives.
        """
        buf = self._tail + data
        shift = len(self._tail)
        ends: list[int] = []
        pos = 0
        while (i := buf.find(QUIT, pos)) >= 0:
            pos = i + len(QUIT)
            ends.append(pos - shift)
        self._tail = buf[max(pos, len(buf) - (len(QUIT) - 1)) :]
        return ends


def sanitize_relay(text: bytes) -> bytes:
    """Strip every byte outside ``0x20..0x7e`` (II §4.3)."""
    return bytes(b for b in text if _PRINTABLE_LOW <= b <= _PRINTABLE_HIGH)


def _printable(text: str) -> bytes:
    return sanitize_relay(text.encode("latin-1", "ignore"))


def plain_line(text: str) -> bytes:
    """``text`` as a displayed line: printable ASCII only, then CR (I §5.2)."""
    return _printable(text) + b"\r"


def bell() -> bytes:
    """A BEL, which the client beeps for (I §5.2)."""
    return BEL


def arm_plus() -> bytes:
    """The ``0x01`` byte: arms command mode and pre-seeds ``+`` (I §5.2)."""
    return ARM_PLUS


def arm_line(text: str) -> bytes:
    """One armed command line: ``0x05 text CR`` (I §5.2).

    Raises ``WireError`` if ``text`` holds a non-printable byte or the line (text and CR) would
    not be shorter than 512 bytes: that is a bug in our code (DESIGN §5), not input.
    """
    try:
        raw = text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise WireError("armed line holds a non-ASCII character") from exc
    if sanitize_relay(raw) != raw:
        raise WireError("armed line holds a non-printable byte")
    if len(raw) + 1 >= ARMED_LINE_LIMIT:
        raise WireError(f"armed line of {len(raw) + 1} bytes is not shorter than 512")
    return ARM_LINE + raw + b"\r"
