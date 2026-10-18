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
production ``FrameReader``) and the server's frames after the tag is applied. The GEnie logon is
never recorded, since its lines carry passwords. A capture is bounded: at ``max_bytes`` it writes a
note and stops. It is optional and best-effort: a file that cannot be written ends the capture and
is logged, never the connection.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from opencs.connlog import STREAM_GAME, STREAM_LOGIN, ConnectionContext
from opencs.wire.frame import FrameReader

log = logging.getLogger("opencs.capture")

_UNSAFE = re.compile(r"[^A-Za-z0-9]")


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
    ) -> None:
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
    ) -> None:
        self._directory = directory
        self._callsigns = callsigns
        self._max = max_bytes
        self._stamp = stamp

    def new(self, context: ConnectionContext, started: float) -> Capture:
        return Capture(self._directory, self._callsigns, self._max, context, started, self._stamp())
