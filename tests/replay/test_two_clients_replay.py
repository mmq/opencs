"""The two-client run 2026-10-16-154139 (I App. A.2) through a City: both clients' recorded frames,
interleaved by time, must make the city do what the test server did, at the protocol level
(introductions, the relay, the Grenade relay, deaths, the leave). Tags and timings may differ."""

from __future__ import annotations

from fixtures.captures import CapturedFrame, load_capture
from fixtures.delivery import deliveries
from fixtures.lobbies import city
from opencs.messages import (
    ClientMessage,
    ServerMessage,
    client,
    decode_client,
    decode_server,
    server,
)
from opencs.wire.frame import FrameReader
from opencs.world.city import City, JoinResult

P1_LOG = "2026-10-16-154139_004-grenade-relay-p1.txt"
P2_LOG = "2026-10-16-154139_005-grenade-relay-p2.txt"

#: P2's log starts 6.86 s after P1's: its first Grenade arrives at 33.657 in P2's log and was
#: thrown at 40.518 in P1's (A.3b: "P2's clock is 6.7 s behind").
P2_CLOCK_OFFSET = 40.518 - 33.657


def payload(frame: CapturedFrame) -> bytes:
    return FrameReader().feed(frame.raw)[0].payload


def recorded(name: str) -> list[ServerMessage]:
    """What the test server sent this client, decoded, in order."""
    return [decode_server(payload(f))[0] for f in load_capture(name) if f.direction == "S"]


def client_frames(name: str, player: int, offset: float) -> list[tuple[float, int, ClientMessage]]:
    return [
        (f.t + offset, player, decode_client(payload(f)))
        for f in load_capture(name)
        if f.direction == "C"
    ]


def run() -> tuple[dict[int, list[ServerMessage]], City]:
    c = City(city("A", name="CYBERCITY ONE"))
    ids = []
    for callsign in ("JOINGENI", "JOINGEN2"):
        r = c.join(callsign, 0, 0.0)
        assert isinstance(r, JoinResult)
        ids.append(r.player_id)
    assert ids == [0, 1]
    frames = client_frames(P1_LOG, 0, 0.0) + client_frames(P2_LOG, 1, P2_CLOCK_OFFSET)
    # P1's log ends with its line dropping (no 'C'): the test server deleted its pod on P2 then.
    frames.append((max(f[0] for f in frames if f[1] == 0) + 0.001, 0, client.Quit()))
    frames.sort(key=lambda f: f[0])  # stable: P1 first on a tie
    got: dict[int, list[ServerMessage]] = {0: [], 1: []}
    for t, player, msg in frames:
        if isinstance(msg, client.Quit):
            events = c.leave(player, t)
        else:
            events = c.client_message(player, msg, t)
        for pid, out in deliveries(c, events):
            got[pid].append(out)
    return got, c


def of_type[M: ServerMessage](msgs: list[ServerMessage], kind: type[M]) -> list[M]:
    return [m for m in msgs if isinstance(m, kind)]


def test_each_client_is_introduced_to_the_other_exactly_once_as_the_test_server_did() -> None:
    got, _ = run()
    for pid, name in ((0, P1_LOG), (1, P2_LOG)):
        ours = of_type(got[pid], server.Introduce)
        theirs = of_type(recorded(name), server.Introduce)
        assert len(ours) == 1 and ours == theirs, name


def test_every_g_is_followed_by_its_a_on_the_same_client() -> None:
    got, _ = run()
    for pid in (0, 1):
        msgs = got[pid]
        for i, m in enumerate(msgs):
            if isinstance(m, server.Introduce):
                assert msgs[i + 1] == server.ReleaseDrop(m.id)


def test_grenades_are_relayed_to_the_other_client_as_the_test_server_did() -> None:
    got, _ = run()
    for pid, name in ((0, P1_LOG), (1, P2_LOG)):
        ours = of_type(got[pid], server.GrenadeInFlight)
        theirs = of_type(recorded(name), server.GrenadeInFlight)
        assert len(ours) == 3 and ours == theirs, name


def test_the_relayed_b_never_names_the_receiver_and_uses_only_states_0_and_1() -> None:
    got, _ = run()
    for pid in (0, 1):
        relayed = of_type(got[pid], server.PodState)
        assert relayed and all(m.id == 1 - pid and m.state in (0, 1) for m in relayed)


def test_each_relayed_b_is_a_position_the_reporter_sent() -> None:
    """The test server also sent server-made 'b' (its own pod's teleports, settled heights) that
    are not a relay; ours is the relay alone, so each one must be a client's own report."""
    got, _ = run()
    for pid, name in ((0, P1_LOG), (1, P2_LOG)):
        other = P2_LOG if pid == 0 else P1_LOG
        reported = {
            (m.pos, m.orient)
            for m in (decode_client(payload(f)) for f in load_capture(other) if f.direction == "C")
            if isinstance(m, client.PodState)
        }
        relayed = of_type(got[pid], server.PodState)
        assert {(m.pos, m.orient) for m in relayed} <= reported
        assert len(relayed) >= len(of_type(recorded(name), server.PodState)) // 2


def test_deaths_are_broadcast_and_a_leaver_is_deleted_on_the_other_client() -> None:
    got, c = run()
    for pid, name in ((0, P1_LOG), (1, P2_LOG)):
        assert of_type(got[pid], server.Destroy) == of_type(recorded(name), server.Destroy)
    assert of_type(got[1], server.Delete) == [server.Delete(0)]  # P1 quit first, as in P2's log
    assert [p.id for p in c.players()] == [1]
