"""Turn one reference-server capture log into a compact fixture (dev tool, not part of the package).

    uv run python tools/make_fixture.py LOG [-o OUT] [--source NAME] [--chamber] [--info-frames]

The capture logs hold ``game rx`` lines (bytes the client sent, in small chunks)
and ``game tx`` lines (whole frames the server sent). Chunks are reassembled with the production
``FrameReader``, so the fixture holds exactly the frames our server would have framed. Output, one
line per frame (see ``tests/fixtures/README.md``)::

    # source: <run>/<file>
    <t seconds, 3 decimals> <C|S> <hex of the whole frame>
    <t> c|s <hex of chamber text>          (only with --chamber)

``C`` is client to server (the frame without its terminator), ``S`` server to client (the frame
with the terminator the server sent). No frame is ever altered.

Some older logs carry no ``game tx`` lines; the server's frames are then only in the INFO lines
that annotate them, as a trailing hex dump (``... install 11 = Immunize  43 53 .. 0d``).
``--info-frames`` lifts those frames as ``S`` lines, and only for a log that has no ``game tx``
lines at all.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections.abc import Iterator
from pathlib import Path

from opencs.wire.frame import TERMINATORS, FrameReader

_LINE = re.compile(
    r"^(?:\d{4}-\d\d-\d\d )?(\d\d):(\d\d):(\d\d\.\d+) DEBUG (game (?:rx|tx)|chamber (?:<-|->))"
    r"\s*(?:\d+: )?(.*)$"
)
_INFO_LINE = re.compile(r"^(?:\d{4}-\d\d-\d\d )?(\d\d):(\d\d):(\d\d\.\d+) INFO (.*)$")
#: A whole server frame (``CS`` header to terminator) dumped at the end of an INFO line.
_FRAME_HEX = re.compile(r"(43 53 (?:[0-9a-f]{2} )+[0-9a-f]{2})\s*$")
_BYTES_LITERAL = re.compile(r"^b(['\"])(.*)\1$")
_HEX = re.compile(r"^[0-9a-f ]+$")

_DAY = 86400.0


def parse_literal(text: str) -> bytes:
    """Bytes of a ``b'...'`` repr as the capture log wrote it (escapes included)."""
    m = _BYTES_LITERAL.match(text.strip())
    if m is None:
        raise ValueError(f"not a bytes literal: {text!r}")
    try:
        value = ast.literal_eval(m.group(0))
    except (ValueError, SyntaxError) as exc:
        raise ValueError(f"bad bytes literal: {text!r}") from exc
    if not isinstance(value, bytes):
        raise ValueError(f"not bytes: {text!r}")
    return value


def log_events(path: Path, *, info_frames: bool = False) -> Iterator[tuple[float, str, bytes]]:
    """``(seconds since the log's first line, kind, data)`` per game or chamber line.

    ``kind`` is ``rx`` or ``tx`` (game bytes) or ``c``/``s`` (chamber text from / to the client).
    Day rollover is handled; old logs stamp only the time of day. With ``info_frames``, a log
    with no ``game tx`` line yields the frames dumped on its INFO lines as ``tx``.
    """
    lines = path.read_text(errors="replace").splitlines()
    lift = info_frames and not any(" DEBUG game tx" in line for line in lines)
    first: float | None = None
    day = 0.0
    last = 0.0
    for line in lines:
        m = _LINE.match(line)
        info = _INFO_LINE.match(line) if m is None and lift else None
        if m is None and info is None:
            continue
        found = m or info
        assert found is not None
        h, mi, s = found.group(1, 2, 3)
        stamp = int(h) * 3600 + int(mi) * 60 + float(s)
        if first is None:
            first = stamp
        if stamp + day < last:
            day += _DAY
        last = stamp + day
        t = last - first
        if info is not None:
            dump = _FRAME_HEX.search(info.group(4))
            if dump is not None:
                yield t, "tx", bytes.fromhex(dump.group(1))
            continue
        assert m is not None
        what, rest = m.group(4, 5)
        if what.startswith("game"):
            if _HEX.match(rest):
                yield t, what.split()[1], bytes.fromhex(rest)
        else:
            try:
                data = parse_literal(rest)
            except ValueError:
                continue
            yield t, "c" if what.endswith("<-") else "s", data


def fixture_lines(path: Path, *, chamber: bool = False, info_frames: bool = False) -> Iterator[str]:
    """The fixture body for one log."""
    reader = FrameReader()
    for t, kind, data in log_events(path, info_frames=info_frames):
        stamp = f"{t:.3f}"
        if kind == "rx":
            for frame in reader.feed(data):
                yield f"{stamp} C {frame.raw.hex()}"
        elif kind == "tx":
            yield from _server_frames(stamp, data)
        elif chamber:
            yield f"{stamp} {kind} {data.hex()}"


def _server_frames(stamp: str, data: bytes) -> Iterator[str]:
    """One line per frame in a ``tx`` chunk, each with its terminator when the server sent one."""
    pos = 0
    for frame in FrameReader().feed(data):
        start = data.find(frame.raw, pos)
        end = start + len(frame.raw)
        if end < len(data) and data[end] in TERMINATORS:
            end += 1
        yield f"{stamp} S {data[start:end].hex()}"
        pos = end


def main(argv: list[str] | None = None) -> int:
    """Command line entry."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("log", type=Path)
    parser.add_argument("-o", "--output", type=Path, help="write here instead of stdout")
    parser.add_argument("--source", help="name for the header (default: <run dir>/<file>)")
    parser.add_argument("--chamber", action="store_true", help="also keep the Chamber text lines")
    parser.add_argument(
        "--info-frames",
        action="store_true",
        help="for a log with no 'game tx' lines, lift the server frames dumped on INFO lines",
    )
    args = parser.parse_args(argv)
    source = args.source or f"{args.log.parent.name}/{args.log.name}"
    lines = [
        f"# source: {source}",
        *fixture_lines(args.log, chamber=args.chamber, info_frames=args.info_frames),
    ]
    text = "\n".join(lines) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
