"""Load the capture fixtures in ``tests/fixtures/captures`` (format: see ``../README.md``)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
    frames: list[CapturedFrame] = []
    for line in (DIRECTORY / name).read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        t, kind, hexdata = line.split(" ", 2)
        if kind in ("C", "S"):
            frames.append(CapturedFrame(float(t), kind, bytes.fromhex(hexdata)))
    return frames
