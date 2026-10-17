"""I App. A.1 and I App. A.2: every annotated frame decodes to the stated field values (message
level)."""

from __future__ import annotations

from fixtures.spec_vectors import FULL_FRAMES, PAYLOADS
from opencs.messages import (
    WirePos,
    WireXZ,
    client,
    client_payload,
    decode_client,
    decode_server,
    server,
    server_payload,
)
from opencs.wire.frame import FrameReader, build_frame


def payload_of(name: str) -> bytes:
    frames = FrameReader().feed(FULL_FRAMES[name][1])
    assert len(frames) == 1
    return frames[0].payload


def test_a3_session_banner() -> None:
    msg, tag = decode_server(payload_of("A3_B"))
    assert tag == 0x11
    assert msg == server.Session(
        self_id=0,
        index=0,
        team=0,
        callsign=b"JOINGENI",
        network=b"GENIE",
        city=b"CYBERCITY ONE",
        max_mult=8,
        tower_output=1,
        max_module_level=2,
        n4=0,
        n5=0,
    )


def test_a3_flags_then_drop_release() -> None:
    assert decode_client(payload_of("A3_S")) == client.Flags(0)
    msg, tag = decode_server(payload_of("A3_A"))
    assert (msg, tag) == (server.ReleaseDrop(0), 0x20)


def test_a3_first_pod_state_in_drop() -> None:
    pod = decode_client(payload_of("A3_b"))
    assert pod == client.PodState(
        pos=WirePos(0, 0, 145, 240, 5),
        orient=450,
        fwd=0,
        fwd_target=0,
        v1=0,
        v2=0,
        state=4,
        power=3000,
        weight=0,
        armor0=0,
        armor1=0,
    )


def test_a3_ping_and_reply() -> None:
    assert decode_client(payload_of("A3_EN")) == client.Ping()
    assert decode_server(payload_of("A3_EN_reply")) == (server.PingReply(), 0x27)


def test_a3_acks_and_sentinel_hit() -> None:
    assert decode_client(payload_of("A3_d")) == client.Acks(bytes([0x11, 0x12, 0x13, 0x14, 0x15]))
    assert decode_client(payload_of("A3_I")) == client.HitReport(target=4096, damage=0)


def test_a3_loose_payloads() -> None:
    assert decode_client(PAYLOADS["A3_M"]) == client.TowerOrder(
        WirePos(0, 0, 224, 62, 332), mega=0, priority=0
    )
    assert decode_client(PAYLOADS["A3_K"]) == client.LegArrived(250)
    assert decode_client(PAYLOADS["A3_Q"]) == client.PickupRequest(32)


def test_a3b_introduction_of_the_second_pod() -> None:
    msg, tag = decode_server(payload_of("A3b_G"))
    assert tag == 0x3A
    assert msg == server.Introduce(
        id=1,
        callsign=b"JOINGEN2",
        index=1,
        team=0,
        pos=WirePos(0, 0, 75, 240, 75),
        orient=450,
        v1=0,
        v2=0,
        bounty_div10=0,
    )
    assert decode_server(payload_of("A3b_A")) == (server.ReleaseDrop(1), 0x3B)


def test_a3b_relayed_pod_state() -> None:
    msg, tag = decode_server(payload_of("A3b_b"))
    assert tag is None
    assert msg == server.PodState(
        id=1,
        pos=WirePos(0, 0, 75, 240, 75),
        orient=450,
        fwd=0,
        fwd_target=0,
        v1=0,
        v2=0,
        state=0,
        weight=0,
    )


def test_a3b_grenade_both_directions() -> None:
    pos = WirePos(2, 2, 100, 72, 300)
    assert decode_client(payload_of("A3b_E_grenade_c2s")) == client.Grenade(pos, 869, 3332)
    assert decode_server(payload_of("A3b_E_grenade_s2c")) == (
        server.GrenadeInFlight(pos, 869, 3332),
        0x87,
    )
    assert (pos.world_x, pos.world_z) == (2 * 600 + 100, 2 * 600 + 300)


def test_a3b_deaths() -> None:
    assert decode_server(payload_of("A3b_J_s2c")) == (server.Destroy(1), 0x4D)  # P2's pod
    assert decode_server(payload_of("A3b_J0_s2c")) == (server.Destroy(0), 0x4F)
    assert decode_client(payload_of("A3b_J_c2s")) == client.Died()
    assert decode_client(payload_of("A3b_A_c2s")) == client.DropMe()


def test_a3b_shot_and_hit() -> None:
    shot = decode_client(PAYLOADS["A3b_c"])
    assert shot == client.Shot(
        shooter=0, pos=WirePos(1, 1, 207, 212, 235), orient=3196, pitch=0, kind=1
    )
    assert decode_client(PAYLOADS["A3b_I_c2s"]) == client.HitReport(target=1, damage=0)


def test_a3b_chat_in_and_echo_out() -> None:
    assert decode_client(payload_of("A3b_U")) == client.Chat(0, b"ok")
    assert decode_server(payload_of("A3b_a")) == (server.Chat(0, 0, b"ok"), None)


def test_a3b_quit_handshake() -> None:
    assert decode_client(payload_of("A3b_C_c2s")) == client.Quit()
    assert decode_server(payload_of("A3b_C_s2c")) == (server.QuitAck(), 0x65)


def test_every_vector_reencodes_to_the_captured_frame() -> None:
    for name, (direction, frame) in FULL_FRAMES.items():
        payload = payload_of(name)
        if direction == "C2S":
            rebuilt = build_frame(client_payload(decode_client(payload)), terminator=b"")
            assert rebuilt == frame.rstrip(b"\x0a"), name
        else:
            msg, tag = decode_server(payload)
            assert build_frame(server_payload(msg, tag or 0)) == frame, name


def test_position_helpers_agree_with_the_vectors() -> None:
    assert WirePos.from_world(1 * 600 + 207, 212, 1 * 600 + 235) == WirePos(1, 1, 207, 212, 235)
    assert WireXZ.from_world(5 * 600 + 250, 6 * 600 + 333) == WireXZ(5, 6, 250, 333)
    far = WirePos.from_world(-30, 5, 4850)
    assert (far.cell_x, far.x_off, far.cell_z, far.z_off) == (0, -30, 7, 650)
    assert (far.world_x, far.world_z) == (-30, 4850)
