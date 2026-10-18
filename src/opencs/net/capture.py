"""Optional wire capture of one connection, in the replay-fixture format (DESIGN §6).

A capture is a text file, one line per event, exactly the format of ``tests/fixtures/captures``
(see ``tests/fixtures/README.md``)::

    # source: <what this is>
    <t> C <hex>    a whole game frame from the client, no terminator
    <t> S <hex>    a whole game frame to the client, with its terminator
    <t> c <hex>    Chamber text from the client (the bytes of one read)
    <t> s <hex>    Chamber text to the client

``t`` is seconds since the connection began, three decimals. Frames are taken from the net layer
at the two boundaries: the client's bytes as they arrive (reassembled into frames with the
production ``FrameReader``) and the server's frames after the tag is applied. What the client types
at the GEnie logon is never recorded, since its lines carry passwords; the server's own prompts may
appear (they hold nothing secret). A capture is bounded: at ``max_bytes`` it writes a
note and stops. It is optional and best-effort: a file that cannot be written ends the capture and
is logged, never the connection.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from opencs import policy
from opencs.connlog import STREAM_GAME, STREAM_LOGIN, ConnectionContext
from opencs.wire.frame import FrameReader

log = logging.getLogger("opencs.capture")

_UNSAFE = re.compile(r"[^A-Za-z0-9]")

#: The names this module gives its files (``<stamp>_<id>-<callsign>.txt``); only these are ever
#: pruned, so a fixture an operator saved in the same directory is left alone.
_OWN_FILE = re.compile(r"\d{4}-\d{2}-\d{2}-\d{6}_\d+-\w*\.txt")


def make_room(directory: Path, room: int, limit: int) -> bool:
    """Remove the oldest capture files in ``directory`` until ``room`` more bytes fit under
    ``limit`` in total. False when they cannot (the directory cannot be read or a file not
    removed), in which case no capture is made."""
    try:
        files = sorted(
            (f for f in directory.iterdir() if _OWN_FILE.fullmatch(f.name)),
            key=lambda f: (f.stat().st_mtime, f.name),
        )
        total = sum(f.stat().st_size for f in files)
        for old in files:
            if total + room <= limit:
                break
            size = old.stat().st_size
            old.unlink()
            total -= size
            log.warning("capture directory over its %d-byte cap: removed %s", limit, old)
    except OSError:
        log.warning("could not make room in %s; capture off", directory, exc_info=True)
        return False
    return total + room <= limit


class Capture:
    """One connection's capture file, opened when the first thing worth recording happens."""

    def __init__(
        self,
        directory: Path,
        callsigns: frozenset[str],
        max_bytes: int,
        context: ConnectionContext,
        started: float,
        stamp: str,
        total_max: int = policy.CAPTURE_DIR_MAX_BYTES,
    ) -> None:
        self._total_max = total_max
        self._directory = directory
        self._callsigns = callsigns
        self._max = max_bytes
        self._context = context
        self._started = started
        self._stamp = stamp
        self._path = directory
        self._created = False
        self._file: TextIO | None = None
        self._size = 0
        self._off = False  # filtered out, full, or unwritable: nothing more is recorded
        self._frames = FrameReader()
        self._reading_frames = False

    @property
    def path(self) -> Path | None:
        """The file, once one has been made."""
        return self._path if self._created else None

    def client_bytes(self, data: bytes, now: float) -> None:
        """Bytes just read from the socket, before the session sees them."""
        stream = self._context.stream
        if self._off or stream == STREAM_LOGIN:
            return
        if stream == STREAM_GAME:
            if not self._reading_frames:
                self._reading_frames, self._frames = True, FrameReader()
            for frame in self._frames.feed(data):
                self._line(now, "C", frame.raw)
        else:
            self._reading_frames = False
            self._line(now, "c", data)

    def server_data(self, data: bytes, *, framed: bool, now: float) -> None:
        """What is about to be written to the socket: a framed message or raw text."""
        if self._off or (not framed and self._context.stream == STREAM_LOGIN):
            return
        self._line(now, "S" if framed else "s", data)

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                log.warning("could not close %s", self._path, exc_info=True)
            self._file = None
        self._off = True

    # --- internals ---------------------------------------------------------------------------

    def _line(self, now: float, kind: str, data: bytes) -> None:
        if self._file is None and not self._open():
            return
        assert self._file is not None
        text = f"{max(0.0, now - self._started):.3f} {kind} {data.hex()}\n"
        if self._size + len(text) > self._max:
            self._write(f"# truncated: capture reached {self._max} bytes\n")
            self.close()
            return
        self._write(text)

    def _write(self, text: str) -> None:
        assert self._file is not None
        try:
            self._file.write(text)
            self._size += len(text)
        except OSError:
            log.warning("could not write %s; capture stopped", self._path, exc_info=True)
            self.close()

    def _open(self) -> bool:
        """Create the file at the first recorded line; False when this connection is not to be
        captured (its callsign is not on the list) or the file cannot be made."""
        callsign = self._context.callsign
        if self._callsigns and callsign not in self._callsigns:
            self._off = True
            return False
        name = _UNSAFE.sub("_", callsign)
        self._path = self._directory / f"{self._stamp}_{self._context.conn_id:03d}-{name}.txt"
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            if not make_room(self._directory, min(self._max, self._total_max), self._total_max):
                self._off = True
                return False
            self._file = self._path.open("x", encoding="ascii", buffering=1)
        except OSError:
            log.warning("could not create %s; capture off", self._path, exc_info=True)
            self._off = True
            return False
        self._created = True
        log.info("capturing %s", self._path)
        self._write(f"# source: opencs connection {self._context.conn_id} ({callsign})\n")
        return True


class CaptureFactory:
    """Makes a ``Capture`` for each accepted connection. ``stamp`` names the start of each
    (``YYYY-MM-DD-HHMMSS``); the app supplies the wall clock, tests a fixed one."""

    def __init__(
        self,
        directory: Path,
        callsigns: frozenset[str],
        max_bytes: int,
        stamp: Callable[[], str],
        total_max: int = policy.CAPTURE_DIR_MAX_BYTES,
    ) -> None:
        self._total_max = total_max
        self._directory = directory
        self._callsigns = callsigns
        self._max = max_bytes
        self._stamp = stamp

    def new(self, context: ConnectionContext, started: float) -> Capture:
        return Capture(
            self._directory,
            self._callsigns,
            self._max,
            context,
            started,
            self._stamp(),
            self._total_max,
        )
