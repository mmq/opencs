"""The in-game session: 'B' and its repeat, the ping, the drop and death handshakes, the quit
handshake and the return to the Chamber (I §5.5, I §7.1, I §7.5, II §7, I §8.7, I App. A.1, I App.
A.2, I §7.8)."""

from __future__ import annotations

from fixtures.pods import report
from fixtures.sessions import Harness, World, enter_lobby, make_world, play
from opencs import policy
from opencs.messages import (
    ClientMessage,
    ServerMessage,
    client,
    client_payload,
    decode_client,
    server,
    server_payload,
)
from opencs.session.chamber import ChamberPhase
from opencs.session.ingame import InGamePhase
from opencs.wire.codec import BYTE_MIN
from opencs.wire.frame import build_frame
from opencs.world.city import City

# I App. A.1: the client's first frames, payload hex as logged (back to back, no terminator).
A3_S = bytes.fromhex("5310")
A3_B_STATE_4 = bytes.fromhex("62 10 b2 19 12 20 60 12 2e 88 88 10 10 14 12 d8 10 10 10")
A3_PING = bytes.fromhex("454e")
QUIT = b"QUIT\r"


def frame(msg: ClientMessage) -> bytes:
    return build_frame(client_payload(msg), terminator=b"\x0a")


def raw_frame(payload: bytes) -> bytes:
    return build_frame(payload, terminator=b"")


def city_a(world: World) -> City:
    return world.services.cities["A"]


def sent(h: Harness) -> list[ServerMessage]:
    """Messages sent so far, clearing the list."""
    out = list(h.io.messages)
    h.io.messages.clear()
    return out


def tick_city(world: World, h: Harness) -> None:
    """Advance 0.2 s and run the city's ticker (the app does this at ``TICK_HZ``)."""
    h.advance(1 / policy.TICK_HZ)
    city = city_a(world)
    world.services.router.deliver(city, city.tick(world.clock.now()))


def phase(h: Harness) -> InGamePhase:
    assert isinstance(h.session.phase, InGamePhase)
    return h.session.phase


# --- 'B' and its repeat ------------------------------------------------------------------------


def test_entering_the_game_seats_the_player_and_sends_b_at_once() -> None:
    world = make_world()
    h = play(world)
    (hello,) = h.io.messages
    assert isinstance(hello, server.Session)
    assert (hello.self_id, hello.index, hello.team) == (0, 0, 0)
    assert hello.callsign == b"JOINGENI" and hello.city == b"CITY A"
    assert [p.id for p in city_a(world).players()] == [0]
    assert world.lobby.find("JOINGENI") is not None


def test_b_is_repeated_every_point_two_seconds_until_the_first_frame() -> None:
    h = play(make_world())
    h.advance(policy.B_REPEAT_INTERVAL - 0.05)
    assert len(h.io.messages) == 1
    h.advance(0.05)
    assert len(h.io.messages) == 2
    h.advance(policy.B_REPEAT_INTERVAL)
    assert len(h.io.messages) == 3
    assert len({m for m in h.io.messages if isinstance(m, server.Session)}) == 1  # all the same B


def test_the_first_frame_from_the_client_stops_the_repeat() -> None:
    h = play(make_world())
    h.feed(raw_frame(A3_S))
    count = len(h.io.messages)
    h.advance(5.0)
    assert [m for m in h.io.messages[count:] if isinstance(m, server.Session)] == []


def test_b_repeats_stop_before_the_3500_byte_cap_would_be_passed() -> None:
    h = play(make_world())
    size = len(build_frame(server_payload(h.io.messages[0], BYTE_MIN)))
    for _ in range(200):
        h.advance(policy.B_REPEAT_INTERVAL)
    total = sum(1 for m in h.io.messages if isinstance(m, server.Session)) * size
    assert total <= policy.B_REPEAT_CAP_BYTES
    assert total + size > policy.B_REPEAT_CAP_BYTES  # it went right up to the cap


#: What a first report is answered with after the drop's ``'A'``: the session-start status, bays
#: first (I §9.2.11, II §11.3). The test city's floor is its max module level, 2.
START = [
    server.SetBays(policy.NEW_PLAYER_BAYS, policy.NEW_PLAYER_MARKER),
    server.SetStatus(0, 2, 0),
]


# --- the ping, the first drop, A.3 -------------------------------------------------------------


def test_a_ping_is_answered_at_once_and_ahead_of_queued_traffic() -> None:
    h = play(make_world())
    sent(h)
    h.feed(frame(report(4)) + raw_frame(A3_PING))
    assert sent(h) == [server.PingReply(), server.ReleaseDrop(0), *START]


def test_the_first_frames_of_the_worked_session_are_answered_as_in_the_spec() -> None:
    world = make_world()
    h = play(world)
    first_b = decode_client(A3_B_STATE_4)
    assert isinstance(first_b, client.PodState) and first_b.state == 4
    sent(h)
    h.feed(raw_frame(A3_S))  # 'S' announce: not relayed, not answered
    assert sent(h) == []
    h.feed(raw_frame(A3_B_STATE_4))
    assert sent(h) == [server.ReleaseDrop(0), *START]
    tick_city(world, h)
    assert sent(h) == [server.ReleaseDrop(0)]  # the tick repeats it while the pod reports 4
    h.feed(raw_frame(A3_PING))
    assert sent(h) == [server.PingReply()]
    h.feed(frame(report(1, y=229)))  # falling: no more 'A'
    tick_city(world, h)
    tick_city(world, h)
    assert sent(h) == []
    h.feed(raw_frame(b"d" + bytes([0x11, 0x12, 0x13, 0x14, 0x15])))  # acknowledgments
    assert sent(h) == []
    assert not h.io.closed


def test_a_death_and_respawn_through_the_session() -> None:
    world = make_world()
    h = play(world)
    h.feed(frame(report(4)))
    h.feed(frame(report(1)))
    h.feed(frame(report(0)))
    sent(h)
    h.feed(frame(client.Died()))
    assert sent(h) == [server.Destroy(0)]  # 'J' reaches the victim too
    h.feed(frame(report(4)))  # the fly-over
    tick_city(world, h)
    assert sent(h) == []
    h.feed(frame(client.DropMe()))
    assert sent(h) == [server.ReleaseDrop(0)]


def test_an_exception_in_the_city_drops_the_request_and_keeps_the_connection() -> None:
    world = make_world()
    h = play(world)
    city = city_a(world)
    real = city.client_message

    def broken(player_id: int, msg: ClientMessage, now: float) -> list:  # type: ignore[type-arg]
        raise RuntimeError("rule bug")

    city.client_message = broken  # type: ignore[method-assign]
    h.feed(frame(report(4)))
    assert not h.io.closed
    city.client_message = real  # type: ignore[method-assign]
    sent(h)
    h.feed(frame(report(4)))
    assert sent(h) == [server.ReleaseDrop(0), *START]


# --- the quit handshake ------------------------------------------------------------------------


def quit_game(h: Harness) -> None:
    sent(h)
    h.feed(frame(client.Quit()))


def test_the_quit_is_answered_with_a_built_c_and_the_player_leaves_its_city() -> None:
    world = make_world()
    h = play(world)
    quit_game(h)
    assert sent(h) == [server.QuitAck()]  # a message the connection tags, not the client's frame
    assert city_a(world).players() == []
    assert not h.io.closed


def test_after_the_quit_no_more_in_game_packets_reach_the_client() -> None:
    world = make_world()
    h1, h2 = play(world), play(world)
    city = city_a(world)
    other = city.player(1)
    assert other is not None
    other.view.hold(0)  # h2's client was introduced to h1 (M5 does this)
    quit_game(h1)
    assert sent(h1) == [server.QuitAck()]
    assert [m for m in sent(h2) if isinstance(m, server.Delete)] == [server.Delete(0)]
    h2.feed(frame(report(4)))
    h2.feed(frame(report(0)))
    h2.feed(frame(client.Died()))  # 'J' goes to everyone in the city, but h1 has left
    tick_city(world, h1)
    assert sent(h1) == []
    assert [m for m in sent(h2) if isinstance(m, server.Destroy)] == [server.Destroy(1)]


def test_frames_after_the_quit_are_ignored() -> None:
    h = play(make_world())
    quit_game(h)
    sent(h)
    h.feed(raw_frame(A3_PING) + frame(report(4)))
    assert sent(h) == []


def test_two_quit_lines_return_the_connection_to_the_chamber_after_three_seconds() -> None:
    world = make_world()
    h = play(world)
    quit_game(h)
    h.take()
    h.feed(QUIT)
    h.advance(0.5)
    h.feed(QUIT)
    h.advance(policy.RETURN_SETTLE - 0.1)
    assert h.take() == b""
    assert isinstance(h.session.phase, InGamePhase)
    h.advance(0.2)
    assert h.take().startswith(b"\x05A\r")
    assert isinstance(h.session.phase, ChamberPhase)
    member = world.lobby.find("JOINGENI")
    assert member is not None and member.in_chamber and member.team is None


def test_the_settle_runs_from_the_last_quit_not_the_first() -> None:
    h = play(make_world())
    quit_game(h)
    h.take()
    h.feed(QUIT)
    h.advance(0.9)
    h.feed(QUIT)
    h.advance(policy.RETURN_SETTLE - 0.1)  # 3.0 s after the first, 2.9 after the second
    assert h.take() == b""
    h.advance(0.2)
    assert h.take().startswith(b"\x05A\r")


def test_quit_lines_in_the_same_read_as_the_c_frame_count() -> None:
    h = play(make_world())
    sent(h)
    h.take()
    h.feed(frame(client.Quit()) + QUIT + QUIT)
    h.advance(policy.RETURN_SETTLE)
    assert h.take().startswith(b"\x05A\r")


def test_quit_lines_split_across_reads_count() -> None:
    h = play(make_world())
    quit_game(h)
    h.take()
    for chunk in (b"QU", b"IT\rQUIT", b"\r"):
        h.feed(chunk)
    h.advance(policy.RETURN_SETTLE)
    assert h.take().startswith(b"\x05A\r")


def test_one_quit_line_is_enough_once_the_second_wait_has_passed() -> None:
    h = play(make_world())
    quit_game(h)
    h.take()
    h.feed(QUIT)
    h.advance(policy.RETURN_SETTLE - 0.1)
    assert h.take() == b""
    h.advance(0.2)
    assert h.take().startswith(b"\x05A\r")


def test_no_quit_line_within_ten_seconds_closes_the_connection() -> None:
    h = play(make_world())
    quit_game(h)
    h.advance(policy.QUIT_WAIT - 0.1)
    assert not h.io.closed
    h.advance(0.2)
    assert h.io.closed


def test_the_player_plays_again_on_the_same_connection_after_returning() -> None:
    world = make_world()
    h = play(world)
    quit_game(h)
    h.feed(QUIT + QUIT)
    h.advance(policy.RETURN_SETTLE)
    h.take()
    h.feed("/PLAY A\r")
    assert h.take().endswith(b"\x05E 1 1\r")
    h.feed("/GO 0\r")
    assert h.take().endswith(b"\x05C\r")
    assert isinstance(h.session.phase, InGamePhase)
    assert any(isinstance(m, server.Session) for m in h.io.messages)
    assert [p.id for p in city_a(world).players()] == [0]  # the emptied city started afresh


def test_playing_again_while_others_stay_takes_a_new_seat() -> None:
    world = make_world()
    h = play(world)
    play(world)  # player 1 stays throughout
    quit_game(h)
    h.feed(QUIT + QUIT)
    h.advance(policy.RETURN_SETTLE)
    h.feed("/PLAY A\r/GO 0\r")
    ids = [p.id for p in city_a(world).players()]
    assert ids == [1, 2]  # id 0 is not handed out again while the range has fresh ids


def test_a_stray_version_reply_after_the_return_is_not_chat() -> None:
    world = make_world()
    h = play(world)
    bystander = enter_lobby(world)
    bystander.take()
    quit_game(h)
    h.feed(QUIT + QUIT)
    h.advance(policy.RETURN_SETTLE)
    h.take()
    h.feed("CSTTBT 105 150\r")
    assert bystander.take() == b""  # swallowed, not relayed as chat
    assert h.take() == b""


# --- socket loss -------------------------------------------------------------------------------


def test_a_line_drop_in_game_frees_the_seat_and_tells_the_others() -> None:
    world = make_world()
    h1, h2 = play(world), play(world)
    other = city_a(world).player(1)
    assert other is not None
    other.view.hold(0)
    sent(h2)
    h1.session.closed()
    assert city_a(world).player(0) is None
    assert sent(h2) == [server.Delete(0)]
    assert world.lobby.find("JOINGENI") is None


def test_a_line_drop_after_the_quit_does_not_leave_twice() -> None:
    world = make_world()
    h = play(world)
    quit_game(h)
    h.session.closed()
    assert city_a(world).players() == []


def test_a_city_that_refuses_the_join_closes_the_connection() -> None:
    world = make_world()
    city = city_a(world)
    for i in range(16):  # fill it behind the lobby's back, as a race would
        assert city.join(f"X{i}", i % 4, 0.0)
    h = play(world)
    assert h.io.closed
    assert "refused" in h.io.closed_with[0]


def test_a_join_that_raises_closes_the_connection_and_seats_nobody() -> None:
    world = make_world()

    def boom(*args: object) -> None:
        raise RuntimeError("bug")

    city_a(world).join = boom  # type: ignore[method-assign,assignment]
    h = play(world)
    assert h.io.closed
    assert city_a(world).players() == []


def test_a_leave_that_raises_is_logged_and_the_quit_goes_on() -> None:
    world = make_world()
    h = play(world)

    def boom(*args: object) -> None:
        raise RuntimeError("bug")

    city_a(world).leave = boom  # type: ignore[method-assign,assignment]
    quit_game(h)
    assert sent(h) == [server.QuitAck()]
    assert not h.io.closed
    h.session.closed()  # and the line drop afterward does not try again


def test_a_quit_line_that_arrives_after_the_return_is_not_chat() -> None:
    """Only one QUIT came before the return; the second is late (I §5.5 step 4, II §4.3)."""
    world = make_world()
    h = play(world)
    bystander = enter_lobby(world)
    bystander.take()
    quit_game(h)
    h.feed(QUIT)
    h.advance(policy.QUIT_SECOND_WAIT + policy.RETURN_SETTLE)
    assert isinstance(h.session.phase, ChamberPhase)
    h.take()
    h.feed(QUIT)
    assert bystander.take() == b""  # swallowed, not relayed
    h.feed(QUIT)
    assert b"JOINGENI: QUIT" in bystander.take()  # only the one that was owed


def test_a_late_quit_line_after_two_were_seen_is_chat_like_any_other() -> None:
    world = make_world()
    h = play(world)
    bystander = enter_lobby(world)
    bystander.take()
    quit_game(h)
    h.feed(QUIT + QUIT)
    h.advance(policy.RETURN_SETTLE)
    h.take()
    h.feed(QUIT)
    assert b"JOINGENI: QUIT" in bystander.take()


def test_a_quit_line_long_after_the_return_is_chat() -> None:
    world = make_world()
    h = play(world)
    bystander = enter_lobby(world)
    bystander.take()
    quit_game(h)
    h.feed(QUIT)
    h.advance(policy.QUIT_SECOND_WAIT + policy.RETURN_SETTLE)
    h.take()
    h.advance(policy.LATE_QUIT_WINDOW)
    h.feed(QUIT)
    assert b"JOINGENI: QUIT" in bystander.take()
