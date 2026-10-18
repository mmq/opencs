"""Wire capture in the replay-fixture format (DESIGN §6, tests/fixtures/README.md)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fixtures.captures import load_capture_file
from fixtures.streams import FakeReader, FakeWriter, wait
from opencs.clock import ManualClock
from opencs.connlog import STREAM_CHAMBER, STREAM_GAME, ConnectionContext
from opencs.messages import client, client_payload, server
from opencs.net.capture import Capture, CaptureFactory
from opencs.net.connection import Connection, ConnectionIO
from opencs.wire.frame import FrameReader, build_frame

FRAME_1 = build_frame(client_payload(client.Chat(0, b"one")), terminator=b"")
FRAME_2 = build_frame(client_payload(client.Chat(1, b"two")), terminator=b"")


def make(
    tmp_path: Path,
    *,
    callsigns: frozenset[str] = frozenset(),
    max_bytes: int = 100_000,
    callsign: str = "ALPHA",
) -> tuple[Capture, ConnectionContext]:
    context = ConnectionContext(7)
    context.callsign = callsign
    factory = CaptureFactory(tmp_path, callsigns, max_bytes, lambda: "2026-10-18-120000")
    return factory.new(context, 100.0), context


def lines(capture: Capture) -> list[str]:
    capture.close()
    assert capture.path is not None
    return capture.path.read_text().splitlines()


def test_chamber_text_and_game_frames_are_written_in_the_fixture_format(tmp_path: Path) -> None:
    capture, context = make(tmp_path)
    context.stream = STREAM_CHAMBER
    capture.server_data(b"\x05A\r", framed=False, now=100.5)
    capture.client_bytes(b"/PLAY A RED\r", 101.0)
    context.stream = STREAM_GAME
    framed = build_frame(b"BN")
    capture.server_data(framed, framed=True, now=101.25)
    # Two frames in three reads, the second split: whole frames only, no terminator.
    capture.client_bytes(FRAME_1 + FRAME_2[:5], 102.0)
    capture.client_bytes(FRAME_2[5:], 102.5)
    assert lines(capture) == [
        "# source: opencs connection 7 (ALPHA)",
        "0.500 s " + b"\x05A\r".hex(),
        "1.000 c " + b"/PLAY A RED\r".hex(),
        "1.250 S " + framed.hex(),
        "2.000 C " + FRAME_1.hex(),
        "2.500 C " + FRAME_2.hex(),
    ]
    assert capture.path == tmp_path / "2026-10-18-120000_007-ALPHA.txt"


def test_the_file_loads_with_the_fixture_loader(tmp_path: Path) -> None:
    capture, context = make(tmp_path)
    context.stream = STREAM_GAME
    framed = build_frame(b"BN")
    capture.server_data(framed, framed=True, now=100.0)
    capture.client_bytes(FRAME_1, 100.1)
    capture.close()
    assert capture.path is not None
    frames = load_capture_file(capture.path)
    assert [(f.t, f.direction, f.raw) for f in frames] == [
        (0.0, "S", framed),
        (0.1, "C", FRAME_1),
    ]


def test_the_logon_is_never_recorded(tmp_path: Path) -> None:
    """The account line carries the password (I §5.1): not in the capture, in either
    direction, and a capture of a connection that never left the logon makes no file."""
    capture, _ = make(tmp_path)
    capture.client_bytes(b"ALPHA,hunter2\r", 100.0)
    capture.server_data(b"PASSWORD: ", framed=False, now=100.1)
    capture.close()
    assert capture.path is None
    assert list(tmp_path.iterdir()) == []


def test_a_capture_stops_at_its_cap_with_a_note(tmp_path: Path) -> None:
    capture, context = make(tmp_path, max_bytes=300)
    context.stream = STREAM_CHAMBER
    for n in range(50):
        capture.client_bytes(b"x" * 20, 100.0 + n)
    found = lines(capture)
    assert found[-1] == "# truncated: capture reached 300 bytes"
    assert capture.path is not None
    assert capture.path.stat().st_size <= 300 + len(found[-1]) + 1
    assert 3 < len(found) < 12  # it recorded some, then stopped (50 reads were offered)


def _old(directory: Path, name: str, size: int, mtime: int) -> Path:
    path = directory / name
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


def test_the_oldest_files_go_when_the_directory_would_pass_its_cap(tmp_path: Path) -> None:
    oldest = _old(tmp_path, "2026-01-01-000000_001-A.txt", 400, 1_000)
    middle = _old(tmp_path, "2026-01-02-000000_002-B.txt", 400, 2_000)
    newest = _old(tmp_path, "2026-01-03-000000_003-C.txt", 400, 3_000)
    keeper = _old(tmp_path, "notes.txt", 5_000, 500)  # not ours: never touched
    context = ConnectionContext(7)
    context.callsign = "ALPHA"
    factory = CaptureFactory(tmp_path, frozenset(), 500, lambda: "2026-10-18-120000", 1_500)
    capture = factory.new(context, 100.0)
    context.stream = STREAM_CHAMBER
    capture.client_bytes(b"hi", 100.0)
    assert capture.path is not None and capture.path.exists()
    # 1200 bytes held + 500 for the new file passes 1500: the oldest goes, and only it (800 + 500).
    assert not oldest.exists() and middle.exists() and newest.exists() and keeper.exists()


def test_a_cap_smaller_than_one_file_still_makes_room_for_a_cap_sized_file(
    tmp_path: Path,
) -> None:
    old = _old(tmp_path, "2026-01-01-000000_001-A.txt", 400, 1_000)
    context = ConnectionContext(7)
    context.callsign = "ALPHA"
    factory = CaptureFactory(tmp_path, frozenset(), 5_000, lambda: "2026-10-18-120000", 1_000)
    capture = factory.new(context, 100.0)
    context.stream = STREAM_CHAMBER
    capture.client_bytes(b"hi", 100.0)
    assert not old.exists() and capture.path is not None and capture.path.exists()


def test_only_listed_callsigns_are_captured(tmp_path: Path) -> None:
    other, context = make(tmp_path, callsigns=frozenset({"BRAVO"}), callsign="ALPHA")
    context.stream = STREAM_CHAMBER
    other.client_bytes(b"hi\r", 100.0)
    other.close()
    assert other.path is None
    listed, context = make(tmp_path, callsigns=frozenset({"ALPHA"}), callsign="ALPHA")
    context.stream = STREAM_CHAMBER
    listed.client_bytes(b"hi\r", 100.0)
    assert len(lines(listed)) == 2


def test_an_unwritable_directory_ends_the_capture_not_the_connection(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    capture, context = make(blocker)
    context.stream = STREAM_CHAMBER
    capture.client_bytes(b"hi\r", 100.0)  # must not raise
    capture.server_data(b"hi", framed=False, now=100.0)
    assert capture.path is None


def test_a_hostile_callsign_cannot_leave_the_directory(tmp_path: Path) -> None:
    capture, context = make(tmp_path, callsign="../../etc/x")
    context.stream = STREAM_CHAMBER
    capture.client_bytes(b"hi\r", 100.0)
    capture.close()
    assert capture.path is not None and capture.path.parent == tmp_path


def test_a_connection_captures_what_it_reads_and_the_tagged_frames_it_writes(
    tmp_path: Path,
) -> None:
    clock = ManualClock(50.0)
    context = ConnectionContext(3)
    context.callsign = "ALPHA"
    capture = CaptureFactory(tmp_path, frozenset(), 100_000, lambda: "stamp").new(context, 50.0)

    class Handler:
        def __init__(self, io: ConnectionIO) -> None:
            self.io = io

        def feed(self, data: bytes) -> None:
            context.stream = STREAM_GAME
            clock.advance(1.0)
            self.io.send(server.PingReply())
            self.io.send_raw(b"text")
            self.io.close("done")

        def tick(self, now: float) -> None: ...

        def closed(self) -> None: ...

    async def scenario() -> bytes:
        reader, writer = FakeReader(), FakeWriter()
        connection = Connection(
            reader,
            writer,
            lambda io, ctx: Handler(io),
            clock,
            context,
            capture=capture,
        )
        context.stream = STREAM_CHAMBER
        reader.push(b"go\r")
        await wait(connection.run())
        return writer.data

    written = asyncio.run(scenario())
    found = (tmp_path / "stamp_003-ALPHA.txt").read_text().splitlines()
    frame_bytes = written[: -len(b"text")]
    assert found[1:] == [
        "0.000 c " + b"go\r".hex(),
        "1.000 S " + frame_bytes.hex(),  # the tag the connection applied is in it
        "1.000 s " + b"text".hex(),
    ]
    assert FrameReader().feed(frame_bytes)[0].payload[1] == 0x10  # the first tag
