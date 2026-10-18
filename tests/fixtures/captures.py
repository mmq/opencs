"""Load the capture fixtures in ``tests/fixtures/captures`` (format: see ``../README.md``)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from opencs.messages import ClientMessage, DecodeError, decode_client
from opencs.wire.frame import FrameReader

DIRECTORY = Path(__file__).parent / "captures"


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    """One captured frame: when (seconds into the log), who sent it, and its whole bytes."""

    t: float
    direction: str  # "C" client to server (no terminator), "S" server to client (with one)
    raw: bytes


def capture_names() -> list[str]:
    """The fixture file names, sorted."""
    return sorted(p.name for p in DIRECTORY.glob("*.txt"))


def load_capture(name: str) -> list[CapturedFrame]:
    """Every game frame in one fixture file, in log order. Chamber lines are skipped."""
    return load_capture_file(DIRECTORY / name)


def load_capture_file(path: Path) -> list[CapturedFrame]:
    """The same, for a capture file anywhere: a fixture, or one the server wrote itself (M12)."""
    frames: list[CapturedFrame] = []
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        t, kind, hexdata = line.split(" ", 2)
        if kind in ("C", "S"):
            frames.append(CapturedFrame(float(t), kind, bytes.fromhex(hexdata)))
    return frames


def client_messages(name: str) -> list[tuple[float, ClientMessage]]:
    """Every decodable client message in one fixture, with its time. Frames that do not decode
    (I §4.1) are skipped, as the session does."""
    out: list[tuple[float, ClientMessage]] = []
    reader = FrameReader()
    for f in load_capture(name):
        if f.direction != "C":
            continue
        for frame in reader.feed(f.raw):
            try:
                out.append((f.t, decode_client(frame.payload)))
            except DecodeError:
                continue
    return out
