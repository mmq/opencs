"""A line drop in the in-game session: socket EOF, or 15 s of silence once POD.FUN has sent a
packet, leaves a record for a real account; a clean quit, a death and a server-side close leave
none (II §13 items 1 and 3, D1, D3, D8)."""

from __future__ import annotations

from fixtures.pods import report
from fixtures.sessions import Harness, World, make_world, play
from opencs import policy
from opencs.messages import ClientMessage, WirePos, client, client_payload, server
from opencs.wire.frame import build_frame
from opencs.world.resume import ResumeRecord

PING = bytes.fromhex("454e")
ALICE = "ALICE,SECRET"
KEY = "acct:ALICE"


def frame(msg: ClientMessage) -> bytes:
    return build_frame(client_payload(msg), terminator=b"\x0a")


def ping(h: Harness) -> None:
    h.feed(build_frame(PING, terminator=b""))


def fly(h: Harness) -> None:
    """The first frames: the drop report, then a landed one."""
    h.feed(frame(report(4)))
    h.feed(frame(report(0)))


def held(world: World, key: str = KEY) -> ResumeRecord | None:
    return world.services.held.get(key)


def alice(world: World, team: int = 1) -> Harness:
    h = play(world, "A", team, ALICE)
    fly(h)
    return h


# --- what creates a record ---------------------------------------------------------------------


def test_socket_eof_in_game_holds_the_game_of_a_real_account() -> None:
    world = make_world()
    h = alice(world, team=2)
    h.session.closed()
    record = held(world)
    assert record is not None and record.eligible
    assert (record.city, record.team, record.callsign) == ("A", 2, "ALICE")
    assert record.dropped_at == world.clock.now()
    assert world.services.held.slots("A") == [(KEY, 2)]
    assert world.services.cities["A"].players() == []


def test_a_guest_who_drops_leaves_no_record() -> None:
    world = make_world()
    h = play(world)
    fly(h)
    h.session.closed()
    assert world.services.held.slots("A") == [] and world.services.held._held == {}
    assert world.services.cities["A"].players() == []


def test_a_clean_quit_creates_no_record() -> None:
    world = make_world()
    h = alice(world)
    h.feed(frame(client.Quit()))
    h.session.closed()  # the connection ends after the quit
    assert held(world) is None


def test_a_death_creates_no_record() -> None:
    world = make_world()
    h = alice(world)
    h.feed(frame(client.Died()))
    assert held(world) is None and world.services.held.slots("A") == []


def test_a_close_by_the_server_is_not_a_line_drop() -> None:
    world = make_world()
    h = alice(world)
    h.session.close("too much bad input")
    h.session.closed()
    assert held(world) is None
    assert world.services.cities["A"].players() == []


def test_an_eof_before_any_frame_is_still_a_line_drop() -> None:
    world = make_world()
    h = play(world, "A", 0, ALICE)  # seated, 'B' sent, nothing heard yet
    h.session.closed()
    record = held(world)
    assert record is not None and record.eligible


# --- silence -----------------------------------------------------------------------------------


def test_silence_for_15_seconds_after_a_frame_is_a_line_drop() -> None:
    world = make_world()
    h = alice(world)
    h.advance(policy.SILENCE_SECONDS - 0.1)
    assert not h.io.closed and held(world) is None
    h.advance(0.1)
    assert h.io.closed_with == ["line silent"]
    record = held(world)
    assert record is not None and record.eligible
    assert world.services.cities["A"].players() == []


def test_silence_is_15_seconds() -> None:
    assert policy.SILENCE_SECONDS == 15.0


def test_any_bytes_restart_the_silence_clock() -> None:
    world = make_world()
    h = alice(world)
    h.advance(10.0)
    ping(h)
    h.advance(10.0)
    assert not h.io.closed
    h.advance(5.0)
    assert h.io.closed


def test_a_partial_frame_counts_as_bytes() -> None:
    world = make_world()
    h = alice(world)
    h.advance(14.0)
    h.feed(b"\x43")  # the first byte of a frame
    h.advance(14.0)
    assert not h.io.closed


def test_a_client_that_has_not_sent_a_frame_is_not_taken_as_silent() -> None:
    world = make_world()
    h = play(world, "A", 0, ALICE)
    h.advance(600.0)
    assert not h.io.closed and held(world) is None


def test_a_guest_silent_for_15_seconds_is_dropped_without_a_record() -> None:
    world = make_world()
    h = play(world)
    fly(h)
    h.advance(policy.SILENCE_SECONDS)
    assert h.io.closed and world.services.held._held == {}
    assert world.services.cities["A"].players() == []


def test_the_socket_closing_after_a_silent_drop_does_not_drop_twice() -> None:
    world = make_world()
    h = alice(world)
    h.advance(policy.SILENCE_SECONDS)
    first = held(world)
    h.session.closed()
    assert held(world) is first


# --- a drop in a fight -------------------------------------------------------------------------


def fight(world: World, bob_team: int = 0) -> tuple[Harness, Harness]:
    """ALICE (team 1) and BOB, both landed at the same spot."""
    a = alice(world)
    b = play(world, "A", bob_team)
    fly(b)
    return a, b


def shoot(b: Harness) -> None:
    b.feed(frame(client.Shot(1, report(0).pos, 0, 0, 1)))


def test_a_drop_under_fire_is_a_death_and_the_record_says_why() -> None:
    world = make_world()
    a, b = fight(world)
    b.io.messages.clear()
    shoot(b)
    a.session.closed()
    sent = b.io.messages
    assert any(isinstance(m, server.Destroy) for m in sent)  # 'J'
    assert any(isinstance(m, server.Delete) for m in sent)  # then 'H'
    assert isinstance(sent[-1], server.Delete)
    record = held(world)
    assert record is not None and not record.eligible and record.reason == "ENEMY FIRE"
    assert world.services.held.slots("A") == []  # nothing to reserve


def test_a_quiet_drop_next_to_a_player_only_deletes_the_pod() -> None:
    world = make_world()
    a, b = fight(world, bob_team=0)
    b.io.messages.clear()
    a.session.closed()
    assert [type(m) for m in b.io.messages] == [server.Delete]


def test_a_guests_drop_in_a_fight_is_a_death_too_with_no_record() -> None:
    world = make_world()
    g = play(world, "A", 1)  # JOINGENIE
    fly(g)
    b = play(world, "A", 0)
    fly(b)
    b.io.messages.clear()
    b.feed(frame(client.Shot(1, report(0).pos, 0, 0, 1)))
    g.session.closed()
    assert any(isinstance(m, server.Destroy) for m in b.io.messages)
    assert world.services.held._held == {}


def test_the_marker_lost_in_a_death_at_the_drop_is_saved() -> None:
    world = make_world()
    a, b = fight(world)
    a.feed(frame(client.Bays((1, 1, 1, 1, 0, 0, 0, 0, 0), 3)))
    shoot(b)
    a.session.closed()
    stored = world.progress.load(KEY)
    assert stored is not None and stored.marker == 2


def test_the_fight_window_runs_from_when_the_line_went_quiet() -> None:
    world = make_world()
    a, b = fight(world)
    world.clock.advance(1.0)
    shoot(b)  # 1 s into the game
    # ALICE stays on the line for another 17 s, then goes quiet; the shot is 17 s before that.
    for _ in range(17):
        a.advance(1.0)
        ping(a)
    a.advance(policy.SILENCE_SECONDS)  # noticed 32 s after the shot
    record = held(world)
    assert record is not None and not record.eligible


# --- keeping the world --------------------------------------------------------------------------


def test_the_held_slot_keeps_the_emptied_city_alive_until_it_expires() -> None:
    world = make_world()
    h = alice(world)
    city = world.services.cities["A"]
    city.orders.place_tower(1, WirePos.from_world(900, 20, 900), False, world.clock.now())
    h.session.closed()
    city.tick(world.clock.now())
    assert len(city.orders.towers) == 1
    world.clock.advance(policy.HOLD_SECONDS)
    city.tick(world.clock.now())
    assert city.orders.towers == {}


def test_a_guests_drop_resets_the_emptied_city() -> None:
    world = make_world()
    h = play(world)
    fly(h)
    city = world.services.cities["A"]
    city.orders.place_tower(0, WirePos.from_world(900, 20, 900), False, world.clock.now())
    h.session.closed()
    assert city.orders.towers == {}
