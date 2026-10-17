"""Real captured traffic through the decoders (fixtures from the test server's run logs)."""

from __future__ import annotations

from collections import Counter

import pytest

from fixtures.captures import CapturedFrame, capture_names, load_capture
from opencs.messages import (
    ClientMessage,
    ServerMessage,
    client,
    client_payload,
    decode_client,
    decode_server,
    server,
    server_payload,
)
from opencs.messages.base import _CLIENT, _SERVER
from opencs.wire.frame import FrameReader

NAMES = capture_names()


def payload_of(frame: CapturedFrame) -> bytes:
    """The payload of a fixture frame, after checking it is exactly one whole frame."""
    frames = FrameReader().feed(frame.raw)
    assert len(frames) == 1, frame
    assert frame.raw.startswith(frames[0].raw)
    assert frame.raw[len(frames[0].raw) :] in (b"", b"\x0a", b"\x0d", b"\x0e")
    return frames[0].payload


def test_the_fixtures_exist() -> None:
    assert len(NAMES) >= 15
    frames = [f for n in NAMES for f in load_capture(n)]
    assert sum(f.direction == "C" for f in frames) > 5000
    assert sum(f.direction == "S" for f in frames) > 5000


#: Client messages whose real frames carry a trailing NUL inside LEN (July to September 2026
#: builds; I §4.1). Re-encoding gives the payload
#: without it.
CLIENT_TRAILING_NUL = (client.PodState,)


def test_every_captured_client_frame_decodes_and_reencodes() -> None:
    trailing = 0
    for name in NAMES:
        for frame in load_capture(name):
            if frame.direction != "C":
                continue
            payload = payload_of(frame)
            msg = decode_client(payload)
            again = client_payload(msg)
            if isinstance(msg, CLIENT_TRAILING_NUL) and payload == again + b"\x00":
                trailing += 1
            else:
                assert payload == again, (name, frame.t)
    # Fails if the fixture holding such frames is dropped.
    assert trailing > 0


@pytest.mark.parametrize("name", NAMES)
def test_every_captured_client_file_decodes(name: str) -> None:
    frames = [f for f in load_capture(name) if f.direction == "C"]
    for frame in frames:
        decode_client(payload_of(frame))
    assert frames


#: Server frames our validation refuses, with why (none in the current fixtures: the test
#: server only sent values the client survives). Keyed by (fixture, seconds, hex of the payload).
KNOWN_SERVER_EXCEPTIONS: dict[tuple[str, float, str], str] = {}


@pytest.mark.parametrize("name", NAMES)
def test_every_captured_server_frame_decodes_and_reencodes_with_its_tag(name: str) -> None:
    # Older runs logged only what the client sent, so a fixture may hold no server frames.
    for frame in load_capture(name):
        if frame.direction != "S":
            continue
        payload = payload_of(frame)
        if (name, frame.t, payload.hex()) in KNOWN_SERVER_EXCEPTIONS:
            continue
        msg, tag = decode_server(payload)
        assert server_payload(msg, tag if tag is not None else 0) == payload, (name, frame.t)


def decoded(direction: str) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    for name in NAMES:
        for frame in load_capture(name):
            if frame.direction == direction:
                payload = payload_of(frame)
                msg = decode_client(payload) if direction == "C" else decode_server(payload)[0]
                out.append((name, msg))
    return out


#: Messages the captures never contain, so only hand-built tests cover them. They are in A.2 /
#: A.1 but the captures never exercised them (I §9.3.5, I §9.5.2, I §8.1).
CLIENT_NOT_CAPTURED = {"AirSupport", "Drone"}
EFFECTS_NOT_CAPTURED = set("WHLMCID")  # only Repair (R) and Call Sentinel (S) were ever used
SERVER_NOT_CAPTURED = {
    "AirSupportReply",
    "DestroyObject",
    "KillNotice",
    "SendMuncher",
    "SetBlock",
    "SetFlags",
    "Shot",
    "SpawnMine",
}


def test_every_a2_message_appears_in_the_captures_except_the_known_gaps() -> None:
    seen = Counter(type(m).__name__ for _, m in decoded("C"))
    classes = {c.__name__ for c in _CLIENT.classes.values()}
    assert classes - set(seen) == CLIENT_NOT_CAPTURED
    effects = {m.effect for _, m in decoded("C") if isinstance(m, client.EffectRequest)}
    assert set(client.EFFECTS) - effects == EFFECTS_NOT_CAPTURED


def test_every_a1_message_appears_in_the_captures_except_the_known_gaps() -> None:
    seen = Counter(type(m).__name__ for _, m in decoded("S"))
    classes = {c.__name__ for c in _SERVER.classes.values()}
    assert classes - set(seen) == SERVER_NOT_CAPTURED


def test_the_captures_hold_the_scenarios_later_milestones_replay() -> None:
    client_msgs = decoded("C")
    server_msgs = decoded("S")
    assert any(isinstance(m, client.Died) for _, m in client_msgs)  # deaths
    assert any(isinstance(m, client.LegArrived) for _, m in client_msgs)  # guppy legs
    assert any(isinstance(m, client.ModuleUsedUp) for _, m in client_msgs)  # Q sub 4
    assert any(isinstance(m, client.ModuleRested) for _, m in client_msgs)  # Q sub 3
    assert any(isinstance(m, client.Grenade) for _, m in client_msgs)
    assert any(isinstance(m, client.Chat) for _, m in client_msgs)
    assert any(isinstance(m, server.Introduce) for _, m in server_msgs)  # two clients
    assert any(isinstance(m, server.MobileCreate) for _, m in server_msgs)  # guppy create
    assert any(isinstance(m, server.SetBlocks) for _, m in server_msgs)
    assert any(isinstance(m, server.Ambient) for _, m in server_msgs)
    assert any(isinstance(m, server.InstallModule) for _, m in server_msgs)


def test_captured_server_pod_states_follow_the_relay_rule() -> None:
    # I §8.24: a relayed 'b' is state 0 or 1 only.
    states = {m.state for _, m in decoded("S") if isinstance(m, server.PodState)}
    assert states <= {0, 1}
    assert states


def test_captured_shots_use_only_safe_kinds() -> None:
    shots = [m for _, m in decoded("C") if isinstance(m, client.Shot)]
    assert shots
    assert all(s.is_safe_kind for s in shots)
    assert {s.reduced_kind for s in shots} <= {1, 2, 3, 9}


def test_captured_pod_ids_are_bytes() -> None:
    for _, m in decoded("S"):
        if isinstance(m, server.ReleaseDrop | server.Delete | server.Introduce | server.PodState):
            assert 0 <= m.id <= 239


def test_client_and_server_messages_are_distinct_hierarchies() -> None:
    assert all(isinstance(m, ClientMessage) for _, m in decoded("C"))
    assert all(isinstance(m, ServerMessage) for _, m in decoded("S"))
