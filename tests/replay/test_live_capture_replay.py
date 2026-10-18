"""A capture the server wrote itself replays in the suite (M12, DESIGN §6).

A scripted client plays a short session against the whole server on a loopback socket with
``[capture]`` on. The file the server wrote is then loaded with the fixture loader the replay tests
use and driven through a fresh city: every frame must decode, and the city must answer the
client's messages with the same server messages, in order, as the live server sent (tags and
times aside). A capture committed as a fixture is replayed the same way, so the format cannot
drift from what the loader reads.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from fixtures.captures import DIRECTORY, CapturedFrame, load_capture_file
from fixtures.loopback import TIMEOUT, TickGate, enter, frame, local_config, start
from fixtures.pods import report
from opencs.app import build_services
from opencs.clock import ManualClock
from opencs.config import CaptureConfig
from opencs.messages import (
    ClientMessage,
    ServerMessage,
    WirePos,
    client,
    decode_client,
    server,
    server_payload,
)
from opencs.messages import decode_server as decode_server_payload
from opencs.routing import Router
from opencs.wire.frame import FrameReader, build_frame
from opencs.world.city import City
from opencs.world.seating import JoinResult

FIXTURE = "2026-10-18-120000_001-live-loopback-p1.txt"


async def play_and_capture(directory: Path) -> Path:
    """One client: first report, open chat, a tower order, then quit. Returns the capture file."""
    config = local_config()
    config = replace(
        config,
        server=replace(
            config.server,
            capture=CaptureConfig(enabled=True, directory=directory),
        ),
    )
    clock, gate = ManualClock(1000.0), TickGate()
    services = build_services(config, clock=clock)
    stop, task, port = await start(config, clock=clock, services=services, tick_sleep=gate.sleep)
    c, _ = await enter(port, "RED")
    await c.send(frame(report(4)))
    assert isinstance(await c.expect_frame("A"), server.ReleaseDrop)
    clock.advance(1.5)
    await c.send(frame(client.Chat(0, b"hello")))
    assert await c.expect_frame("a") == server.Chat(0, 0, b"hello")
    clock.advance(2.0)
    await c.send(frame(client.TowerOrder(WirePos(0, 0, 145, 240, 5), 0, 0)))
    assert isinstance(await c.expect_frame("O"), server.OrderReply)
    await c.send(frame(client.Quit()))
    await c.expect_frame("C")
    c.writer.close()
    stop.set()
    await asyncio.wait_for(task, TIMEOUT)
    found = sorted(directory.glob("*.txt"))
    assert len(found) == 1
    return found[0]


def decoded(frames: list[CapturedFrame]) -> tuple[list[ClientMessage], list[ServerMessage]]:
    """Every ``C`` and ``S`` frame decoded, each line holding exactly one whole frame."""
    sent: list[ClientMessage] = []
    received: list[ServerMessage] = []
    for f in frames:
        reader = FrameReader()
        (parsed,) = reader.feed(f.raw)
        assert reader.bad_frames == 0 and reader.pending == 0
        if f.direction == "C":
            sent.append(decode_client(parsed.payload))
        else:
            received.append(decode_server_payload(parsed.payload)[0])
    return sent, received


def replay(path: Path) -> None:
    frames = load_capture_file(path)
    sent, received = decoded(frames)
    assert received and isinstance(received[0], server.Session)
    hello = received[0]
    shipped_a = next(c for c in local_config().cities if c.letter == "A")  # what the server ran
    fresh = City(shipped_a)
    joined = fresh.join(hello.callsign.decode(), hello.team, 0.0)
    assert isinstance(joined, JoinResult)
    assert joined.hello == hello
    router = Router()
    produced: list[ServerMessage] = [joined.hello]
    times = [f.t for f in frames if f.direction == "C"]
    for t, message in zip(times, sent, strict=True):
        if isinstance(message, client.Quit):
            continue  # answered by the session, not the city
        for event in fresh.client_message(joined.player_id, message, 1000.0 + t):
            if joined.player_id in router.resolve(fresh, event.audience):
                produced.append(event.message)
    assert received[-1] == server.QuitAck()
    assert produced == received[:-1]


def test_a_capture_the_server_wrote_replays_through_a_fresh_city(tmp_path: Path) -> None:
    path = asyncio.run(asyncio.wait_for(play_and_capture(tmp_path), 4 * TIMEOUT))
    frames = load_capture_file(path)
    kinds = [f.direction for f in frames]
    assert kinds.count("C") == 4 and kinds.count("S") >= 5
    assert frames[0].t >= 0.0 and [f.t for f in frames] == sorted(f.t for f in frames)
    replay(path)


def test_the_committed_live_capture_replays() -> None:
    replay(DIRECTORY / FIXTURE)


def test_the_replay_notices_a_changed_server_message(tmp_path: Path) -> None:
    """The comparison is not vacuous: a capture whose chat echo is another (valid) frame fails."""
    path = asyncio.run(asyncio.wait_for(play_and_capture(tmp_path), 4 * TIMEOUT))
    real = build_frame(server_payload(server.Chat(0, 0, b"hello"), 0))
    forged = build_frame(server_payload(server.Chat(0, 0, b"jello"), 0))
    text = path.read_text()
    assert real.hex() in text
    path.write_text(text.replace(real.hex(), forged.hex()))
    with pytest.raises(AssertionError):
        replay(path)
