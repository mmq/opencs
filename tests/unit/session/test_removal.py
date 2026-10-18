"""The server takes a connection out of its game or its Chamber: an admin kick, or shutdown
(I §5.5, I §8.5: an unprompted ``'C'`` returns the client to the Chamber; M12)."""

from __future__ import annotations

from fixtures.pods import report
from fixtures.sessions import Harness, World, enter_lobby, make_world, play
from opencs import policy
from opencs.messages import ClientMessage, client, client_payload, server
from opencs.session.chamber import ChamberPhase
from opencs.session.ingame import InGamePhase
from opencs.wire.frame import build_frame
from opencs.world.resume import ResumeRecord

ALICE = "ALICE,SECRET"
KEY = "acct:ALICE"
NOTICE = "REMOVED BY AN ADMINISTRATOR: BEHAVE"


def frame(msg: ClientMessage) -> bytes:
    return build_frame(client_payload(msg), terminator=b"\x0a")


def fly(h: Harness) -> None:
    h.feed(frame(report(4)))
    h.feed(frame(report(0)))


def alice(world: World, team: int = 1) -> Harness:
    h = play(world, "A", team, ALICE)
    fly(h)
    h.io.messages.clear()
    return h


def held(world: World) -> ResumeRecord | None:
    return world.services.held.get(KEY)


def test_a_kick_in_game_is_a_system_line_then_the_quit_handshake() -> None:
    world = make_world()
    h = alice(world)
    h.session.remove(NOTICE, close=False)
    assert h.io.messages == [
        server.Chat(100, 0, NOTICE.encode()),  # sender 100: the system line (I §8.23)
        server.QuitAck(),  # the unprompted 'C' (I §5.5, I §8.5)
    ]
    assert world.services.cities["A"].players() == []
    assert not h.io.closed


def test_a_kicked_player_comes_back_to_the_chamber_with_the_reason() -> None:
    world = make_world()
    h = alice(world)
    h.session.remove(NOTICE, close=False)
    h.take()
    h.feed(b"QUIT\rQUIT\r")
    h.advance(policy.RETURN_SETTLE + 0.1)
    out = h.take()
    assert isinstance(h.session.phase, ChamberPhase)
    assert out.startswith(b"\x05A\r") and NOTICE.encode() in out
    assert not h.io.closed


def test_a_kick_under_fire_is_not_a_death_and_holds_nothing() -> None:
    """Hanging up in a fight is a death (II §13 item 3); being thrown out is not the
    player's doing."""
    world = make_world()
    a = alice(world)
    b = play(world, "A", 0)
    fly(b)
    b.feed(frame(client.Shot(1, report(0).pos, 0, 0, 1)))  # an attack on ALICE
    b.io.messages.clear()
    a.session.remove(NOTICE, close=False)
    assert not any(isinstance(m, server.Destroy) for m in b.io.messages)  # no 'J'
    assert [type(m) for m in b.io.messages] == [server.Delete]
    assert held(world) is None and world.services.held.slots("A") == []


def test_a_kick_saves_the_players_progress() -> None:
    world = make_world()
    h = alice(world)
    h.feed(frame(client.Bays((1, 1, 1, 1, 0, 0, 0, 0, 0), 3)))
    h.feed(frame(client.Status(777, 1, 88)))
    h.session.remove(NOTICE, close=False)
    stored = world.progress.load(KEY)
    assert stored is not None and (stored.score, stored.bay_points) == (777, 88)
    assert stored.bays == (1, 1, 1, 1, 0, 0, 0, 0, 0)


def test_shutdown_in_game_sends_the_c_then_closes_with_no_fight_test() -> None:
    world = make_world()
    a = alice(world)
    b = play(world, "A", 0)
    fly(b)
    b.feed(frame(client.Shot(1, report(0).pos, 0, 0, 1)))
    b.io.messages.clear()
    a.io.messages.clear()
    a.session.remove("SERVER SHUTTING DOWN", close=True)
    assert [type(m) for m in a.io.messages] == [server.Chat, server.QuitAck]
    assert a.io.closed_with == ["SERVER SHUTTING DOWN"]
    a.session.closed()  # the socket ends afterward
    assert not any(isinstance(m, server.Destroy) for m in b.io.messages)
    assert held(world) is None
    assert [p.id for p in world.services.cities["A"].players()] == [1]  # only BOB remains


def test_a_kick_in_the_chamber_says_why_and_closes() -> None:
    world = make_world()
    h = enter_lobby(world, ALICE)
    h.session.remove(NOTICE, close=False)
    assert NOTICE.encode() in h.take()
    assert h.io.closed


def test_a_kick_of_a_player_already_leaving_does_nothing_more() -> None:
    h = alice(make_world())
    h.feed(frame(client.Quit()))
    h.io.messages.clear()
    h.session.remove(NOTICE, close=False)
    assert h.io.messages == [] and not h.io.closed
    h.session.remove("SERVER SHUTTING DOWN", close=True)
    assert h.io.closed


def test_the_registry_lists_live_sessions_by_callsign() -> None:
    world = make_world()
    h = alice(world)
    other = enter_lobby(world, "BOB,PW")
    registry = world.services.sessions
    assert registry.all() == [h.session, other.session]
    assert registry.by_callsign("ALICE") == [h.session]
    assert registry.by_callsign("NOBODY") == []
    h.session.closed()
    assert registry.all() == [other.session]
    assert isinstance(other.session.phase, ChamberPhase)
    assert not isinstance(other.session.phase, InGamePhase)
