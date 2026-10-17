"""``tools/make_fixture.py`` turns capture-log lines into fixture lines without altering a frame."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from opencs.wire.frame import build_frame

TOOL = Path(__file__).parents[2] / "tools" / "make_fixture.py"


def load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_fixture", TOOL)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def spaced(data: bytes) -> str:
    return " ".join(f"{b:02x}" for b in data)


def test_chunks_are_reassembled_and_tx_frames_keep_their_terminator(tmp_path: Path) -> None:
    client_frame = build_frame(b"S\x10", terminator=b"")
    server_frame = build_frame(b"A\x20\x10")  # ends 0x0d
    lines = [
        "2026-10-16 15:48:21.200 INFO unrelated",
        f"2026-10-16 15:48:22.000 DEBUG game tx {len(server_frame)}: {spaced(server_frame)}",
    ]
    for i, b in enumerate(client_frame):
        lines.append(f"2026-10-16 15:48:22.{100 + i:03d} DEBUG game rx  1: {b:02x}")
    log = tmp_path / "run.log"
    log.write_text("\n".join(lines) + "\n")

    out = list(load_tool().fixture_lines(log))

    last_chunk = 0.1 + 0.001 * (len(client_frame) - 1)  # the frame completes with its last byte
    assert out == [
        f"0.000 S {server_frame.hex()}",
        f"{last_chunk:.3f} C {client_frame.hex()}",
    ]


def test_old_logs_without_a_date_and_a_midnight_rollover(tmp_path: Path) -> None:
    frame = build_frame(b"J", terminator=b"")
    lines = [f"23:59:59.500 DEBUG game rx  1: {b:02x}" for b in frame[:-1]]
    lines.append(f"00:00:01.000 DEBUG game rx  1: {frame[-1]:02x}")
    log = tmp_path / "old.log"
    log.write_text("\n".join(lines) + "\n")

    assert list(load_tool().fixture_lines(log)) == [f"1.500 C {frame.hex()}"]


def test_chamber_lines_only_when_asked(tmp_path: Path) -> None:
    log = tmp_path / "c.log"
    log.write_text(
        "2026-10-16 14:27:01.000 DEBUG chamber -> b'CONNECT\\r\\n'\n"
        "2026-10-16 14:27:01.250 DEBUG chamber <- b'H'\n"
    )
    tool = load_tool()
    assert list(tool.fixture_lines(log)) == []
    assert list(tool.fixture_lines(log, chamber=True)) == [
        f"0.000 s {b'CONNECT\r\n'.hex()}",
        "0.250 c 48",
    ]


def test_a_bad_checksum_frame_is_not_emitted(tmp_path: Path) -> None:
    frame = bytearray(build_frame(b"J", terminator=b""))
    frame[-1] ^= 0x01
    log = tmp_path / "bad.log"
    log.write_text("\n".join(f"2026-10-16 10:00:00.000 DEBUG game rx  1: {b:02x}" for b in frame))
    assert list(load_tool().fixture_lines(log)) == []


def test_server_frames_on_info_lines_are_lifted_only_when_asked_and_only_without_game_tx(
    tmp_path: Path,
) -> None:
    frame = build_frame(b"Q\x10\x15")
    info = f"01:52:36.721 INFO [t=8.9s] MODULE 1/3: install 11  {spaced(frame)}"
    plain = tmp_path / "plain.log"
    plain.write_text("01:52:36.000 INFO start\n" + info + "\n")
    tool = load_tool()
    assert list(tool.fixture_lines(plain)) == []
    assert list(tool.fixture_lines(plain, info_frames=True)) == [f"0.721 S {frame.hex()}"]

    # A log that has game tx lines already holds every server frame: the dumps would repeat them.
    has_tx = tmp_path / "tx.log"
    has_tx.write_text(f"01:52:36.000 DEBUG game tx {len(frame)}: {spaced(frame)}\n{info}\n")
    assert list(tool.fixture_lines(has_tx, info_frames=True)) == [f"0.000 S {frame.hex()}"]
