"""Hostile bytes into each phase of a session: nothing raises, buffers stay inside their limits, bad
input is counted, and the connection closes only for the reasons the accounting allows (DESIGN §5;
I §3, I §5.2, I §4.1)."""

from __future__ import annotations

import random

import pytest

from fixtures.fuzzing import CLIENT_TYPES, ErrorLog, random_client_message
from fixtures.pods import report
from fixtures.sessions import Harness, connect, enter_lobby, make_world, play
from opencs import policy
from opencs.messages import client_payload
from opencs.session.ingame import InGamePhase
from opencs.session.login import LoginPhase
from opencs.session.phases import TextPhase
from opencs.wire.frame import MAX_PENDING, build_frame

#: Why the server may close a connection when all it is sent is hostile bytes.
LOGIN_CLOSES = {
    "login line too long",
    "too many lines before the Chamber",
    "no HHH within the preamble limit",
    "login refused",
    "BYE",
    "login timed out",
}
CHAMBER_CLOSES = {"Chamber flood", "Chamber idle timeout", *LOGIN_CLOSES}
GAME_CLOSES = {"too much bad input", "line silent", "no QUIT after the quit handshake"}


def chunks(rng: random.Random, data: bytes) -> list[bytes]:
    """``data`` cut at random places, down to single bytes."""
    out, i = [], 0
    while i < len(data):
        n = rng.choice([1, 2, 3, 7, 64, 500, 4096])
        out.append(data[i : i + n])
        i += n
    return out


def noise(rng: random.Random, n: int) -> bytes:
    """Random bytes, biased toward the ones the protocol treats specially."""
    special = b"\r\n\x01\x05\x07\x0e\x1b\x18HHHCSQUIT /PLAY/CITY/RESUME,<Not Telling!>"
    return bytes(
        rng.choice(special) if rng.random() < 0.4 else rng.randrange(256) for _ in range(n)
    )


def line_buffer(phase: TextPhase) -> int:
    return len(phase._lines._line)


def assert_clean(h: Harness, allowed: set[str], seed: int) -> None:
    reasons = h.io.closed_with
    assert not reasons or reasons[0] in allowed, f"seed {seed}: closed with {reasons}"


@pytest.mark.parametrize("seed", range(40))
def test_noise_at_the_login_never_raises_and_stays_bounded(seed: int) -> None:
    rng = random.Random(seed)
    world = make_world()
    with ErrorLog() as errors:
        h = connect(world)
        for piece in chunks(rng, noise(rng, rng.randrange(1, 6000))):
            h.feed(piece)
            world.clock.advance(rng.choice([0.0, 0.1, 5.0]))
            h.session.tick(world.clock.now())
            if isinstance(h.session.phase, TextPhase):
                assert line_buffer(h.session.phase) <= policy.MAX_LINE_BYTES
        assert not errors.records, errors.describe()
    assert_clean(h, CHAMBER_CLOSES, seed)


@pytest.mark.parametrize("seed", range(40))
def test_noise_in_the_chamber_never_raises_and_stays_bounded(seed: int) -> None:
    rng = random.Random(seed)
    world = make_world()
    h = enter_lobby(world)
    with ErrorLog() as errors:
        for piece in chunks(rng, noise(rng, rng.randrange(1, 8000))):
            h.feed(piece)
            world.clock.advance(rng.choice([0.0, 0.1, 0.6]))
            h.session.tick(world.clock.now())
            phase = h.session.phase
            if isinstance(phase, TextPhase):
                assert line_buffer(phase) <= policy.MAX_LINE_BYTES
            h.io.raw.clear()  # replies are not the point; keep the recording small
        assert not errors.records, errors.describe()
    assert_clean(h, CHAMBER_CLOSES | {"too much bad input"}, seed)


def good_frame(rng: random.Random, i: int) -> bytes:
    if i % 3 == 0:
        return build_frame(client_payload(report(0, x_off=i % 500)), terminator=b"\x0a")
    return build_frame(client_payload(random_client_message(rng)), terminator=b"\x0a")


def mutate(rng: random.Random, frame: bytes) -> bytes:
    """A frame damaged in one of the ways a line or a modified client damages it."""
    b = bytearray(frame)
    kind = rng.randrange(7)
    if kind == 0:  # a flipped bit anywhere
        b[rng.randrange(len(b))] ^= 1 << rng.randrange(8)
    elif kind == 1:  # truncated
        del b[rng.randrange(1, len(b)) :]
    elif kind == 2:  # a wrong LEN
        b[4] = rng.randrange(256)
    elif kind == 3:  # ASCII text spliced in
        b[rng.randrange(len(b)) : 0] = b"hello\r"
    elif kind == 4:  # 0x05, the arming byte, spliced in
        b[rng.randrange(len(b)) : 0] = b"\x05"
    elif kind == 5:  # a wrong checksum
        b[2] = (b[2] + 1) % 256
    else:  # pure noise
        b = bytearray(noise(rng, rng.randrange(1, 300)))
    return bytes(b)


@pytest.mark.parametrize("seed", range(40))
def test_damaged_frames_in_game_never_raise_and_are_counted(seed: int) -> None:
    rng = random.Random(seed)
    world = make_world()
    h = play(world, "A", 0)
    h.feed(build_frame(client_payload(report(4)), terminator=b"\x0a"))
    phase = h.session.phase
    assert isinstance(phase, InGamePhase)
    with ErrorLog() as errors:
        for i in range(300):
            wire = good_frame(rng, i)
            if rng.random() < 0.5:
                wire = mutate(rng, wire)
            for piece in chunks(rng, wire):
                h.feed(piece)
            world.clock.advance(rng.choice([0.05, 0.2, 0.5]))
            h.session.tick(world.clock.now())
            assert phase._reader.pending <= MAX_PENDING
            if h.io.closed:
                break
            h.io.messages.clear()
        assert not errors.records, errors.describe()
    assert_clean(h, GAME_CLOSES, seed)
    if h.io.closed_with == ["too much bad input"]:
        # Closed by the accounting rule: the window really did hold more than the limit.
        assert phase._bad.total > policy.MAX_BAD_INPUT


def test_a_trickle_of_damaged_frames_does_not_close_an_honest_connection() -> None:
    """A noisy modem: one damaged frame every half second for a minute, between good ones."""
    rng = random.Random(7)
    world = make_world()
    h = play(world, "A", 0)
    for _ in range(120):
        h.feed(good_frame(rng, 0))
        h.feed(mutate(rng, build_frame(client_payload(report(0)), terminator=b"\x0a")))
        world.clock.advance(0.5)
        h.session.tick(world.clock.now())
        h.io.messages.clear()
    assert not h.io.closed


def test_a_sustained_flood_of_garbage_in_game_closes_the_connection() -> None:
    rng = random.Random(1)
    world = make_world()
    h = play(world, "A", 0)
    h.feed(noise(rng, 100) + b"CS\x30\xcf\x40" * 200)
    for _ in range(policy.MAX_BAD_INPUT * 3):
        h.feed(b"CS\x30\xcf\x40" + noise(rng, 10))
    assert h.io.closed_with == ["too much bad input"]


def test_a_partial_frame_that_never_completes_cannot_hold_the_buffer_past_its_bound() -> None:
    world = make_world()
    h = play(world, "A", 0)
    phase = h.session.phase
    assert isinstance(phase, InGamePhase)
    for _ in range(50):
        h.feed(b"CS\x30\xcf\xfe" + b"\x41" * 10)  # a header that claims 238 bytes
        assert phase._reader.pending <= MAX_PENDING


def test_a_login_line_without_a_terminator_is_not_buffered_for_ever() -> None:
    world = make_world()
    h = connect(world)
    h.feed(b"HHH")
    for _ in range(100):
        h.feed(b"A" * 100)
    assert isinstance(h.session.phase, LoginPhase)
    assert line_buffer(h.session.phase) <= policy.MAX_LINE_BYTES
    assert h.io.closed_with == ["login line too long"]


def test_the_message_generator_reaches_every_client_message_type() -> None:
    rng = random.Random(3)
    seen = {type(random_client_message(rng)) for _ in range(4000)}
    assert seen == set(CLIENT_TYPES)
