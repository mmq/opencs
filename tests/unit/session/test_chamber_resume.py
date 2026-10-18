"""The Chamber's half of /RESUME: the notice on coming back, the typing window, the launch into
the held city and team, the reserved slot and giving the game up (II §4.2, II §13, II §13 items 4
and 5, D3, D8, D9)."""

from __future__ import annotations

from fixtures.lobbies import city
from fixtures.pods import report
from fixtures.sessions import Harness, World, login, make_world, play
from opencs import policy
from opencs.messages import ClientMessage, WirePos, client, client_payload, server
from opencs.session.chamber import ChamberPhase
from opencs.session.ingame import InGamePhase
from opencs.wire.frame import build_frame

ALICE = "ALICE,SECRET"
KEY = "acct:ALICE"
CSTTBT = "CSTTBT 105 150\r"


def phase_of(h: Harness) -> type:
    return type(h.session.phase)


def frame(msg: ClientMessage) -> bytes:
    return build_frame(client_payload(msg), terminator=b"\x0a")


def fly(h: Harness) -> None:
    h.feed(frame(report(4)))
    h.feed(frame(report(0)))


def drop_alice(world: World, team: int = 1, *, in_fight: bool = False) -> None:
    """ALICE plays city A, then her line drops (under fire from a guest when ``in_fight``)."""
    a = play(world, "A", team, ALICE)
    fly(a)
    a.feed(frame(client.Status(1670, 5, 957)))
    if in_fight:
        b = play(world, "A", (team + 1) % 4)
        fly(b)
        b.feed(frame(client.Shot(1, report(0).pos, 0, 0, 1)))
        b.session.closed()  # BOB is gone, but his shot stands
    a.session.closed()


def back(world: World, account: str = ALICE) -> tuple[Harness, bytes]:
    """The player logs in again and answers the version check: the Chamber opens."""
    h = login(world, account)
    h.take()
    h.feed(CSTTBT)
    return h, h.take()


def ask(h: Harness, line: str) -> bytes:
    h.take()
    h.feed(line + "\r")
    return h.take()


def launch(h: Harness) -> bytes:
    """``/RESUME`` then the client's ``/GO 0``."""
    out = ask(h, "/RESUME")
    out += ask(h, "/GO 0")
    return out


# --- the notice --------------------------------------------------------------------------------


def test_coming_back_with_a_held_game_tells_the_player_how_long_there_is() -> None:
    world = make_world()
    drop_alice(world)
    world.clock.advance(60.0)
    _, out = back(world)
    assert b"YOUR GAME IN CITY A WAS INTERRUPTED." in out
    assert b"TYPE /RESUME WITHIN 3:00 TO RETURN TO IT." in out


def test_the_notice_also_comes_when_the_player_reenters_after_another_game() -> None:
    world = make_world()
    drop_alice(world)
    h, _ = back(world)
    assert phase_of(h) is ChamberPhase  # the held game is not given up by looking at it


def test_a_guest_is_told_nothing() -> None:
    world = make_world()
    drop_alice(world)
    _, out = back(world, "JOINGENIE")
    assert b"INTERRUPTED" not in out


def test_another_account_is_told_nothing() -> None:
    world = make_world()
    drop_alice(world)
    _, out = back(world, "BOB,SECRET")
    assert b"INTERRUPTED" not in out


def test_a_drop_in_a_fight_is_told_as_a_death() -> None:
    world = make_world()
    drop_alice(world, in_fight=True)
    _, out = back(world)
    assert b"YOUR LINE DROPPED IN CITY A DURING A FIGHT (ENEMY FIRE)." in out
    assert b"THAT COUNTED AS A DEATH: IT CANNOT BE RESUMED." in out
    assert b"INTERRUPTED" not in out


# --- the windows -------------------------------------------------------------------------------


def test_resume_works_up_to_three_minutes_after_entering_the_chamber() -> None:
    world = make_world()
    drop_alice(world)
    h, _ = back(world)
    world.clock.advance(policy.TYPE_SECONDS - 0.5)
    out = ask(h, "/RESUME")
    assert b"RESUMING IN CITY A" in out


def test_resume_is_refused_three_minutes_after_entering_the_chamber() -> None:
    world = make_world()
    drop_alice(world)
    h, _ = back(world)
    world.clock.advance(policy.TYPE_SECONDS)
    assert b"NO GAME IS HELD FOR YOU" in ask(h, "/RESUME")


def test_a_player_back_just_inside_ten_minutes_is_told_and_has_the_three() -> None:
    world = make_world()
    drop_alice(world)
    world.clock.advance(policy.HOLD_SECONDS - 1.0)
    h, out = back(world)
    assert b"WITHIN 3:00" in out
    world.clock.advance(170.0)
    assert b"RESUMING IN CITY A" in ask(h, "/RESUME")


def test_a_player_back_after_ten_minutes_has_nothing_held() -> None:
    world = make_world()
    drop_alice(world)
    world.clock.advance(policy.HOLD_SECONDS)
    h, out = back(world)
    assert b"INTERRUPTED" not in out
    assert b"NO GAME IS HELD FOR YOU" in ask(h, "/RESUME")


# --- /RESUME -----------------------------------------------------------------------------------


def test_resume_launches_the_held_city_and_the_game_is_restored() -> None:
    world = make_world()
    a = play(world, "A", 2, ALICE)
    fly(a)
    a.feed(frame(client.Status(1670, 5, 957)))
    a.feed(frame(client.Bays((3, 2, 1, 1, 5, 4, 0, 1, 2), -2)))
    city_a = world.services.cities["A"]
    alice = city_a.players()[0]
    alice.loadout.modules.extend([3, 31, 12])
    a.session.closed()
    h, _ = back(world)
    out = ask(h, "/RESUME")
    assert b"RESUMING IN CITY A" in out and out.endswith(b"\x05E 1 1\r")
    assert phase_of(h) is ChamberPhase
    out = ask(h, "/GO 0")
    assert out.endswith(b"\x05C\r") and phase_of(h) is InGamePhase
    assert (world.lobby.find("ALICE").city, world.lobby.find("ALICE").team) == ("A", 2)  # type: ignore[union-attr]
    assert world.services.held.get(KEY) is None
    assert h.io.messages[0].__class__ is server.Session
    assert h.io.messages[0].team == 2  # type: ignore[attr-defined]
    h.io.messages.clear()
    h.feed(frame(report(4)))
    h.feed(frame(report(0)))
    sent = h.io.messages
    assert server.SetBays((3, 2, 1, 1, 5, 4, 0, 1, 2), -2) in sent
    assert server.SetStatus(1670, 5, 957) in sent  # held SCORE, multiplier and bay points
    installs = [m.module for m in sent if isinstance(m, server.InstallModule)]
    assert installs == [31, 12, 3]  # highest level first
    assert sent.index(server.SetStatus(1670, 5, 957)) < sent.index(server.InstallModule(31))


def test_resume_without_a_held_game_is_refused_and_stays_in_the_chamber() -> None:
    world = make_world()
    h, _ = back(world)
    assert b"NO GAME IS HELD FOR YOU" in ask(h, "/RESUME")
    assert phase_of(h) is ChamberPhase


def test_resume_is_still_refused_for_joingenie() -> None:
    world = make_world()
    h, _ = back(world, "JOINGENIE")
    assert b"JOINGENIE LOGINS CANNOT RESUME" in ask(h, "/RESUME")


def test_a_fight_drop_is_refused_with_its_reason_and_then_forgotten() -> None:
    world = make_world()
    drop_alice(world, in_fight=True)
    h, _ = back(world)
    out = ask(h, "/RESUME")
    assert b"DURING A FIGHT (ENEMY FIRE)" in out and b"\x05E " not in out
    assert b"NO GAME IS HELD FOR YOU" in ask(h, "/RESUME")


def test_a_failed_launch_leaves_the_game_held_to_try_again() -> None:
    world = make_world()
    drop_alice(world)
    h, _ = back(world)
    ask(h, "/RESUME")
    assert b"MISSING" in ask(h, "/GO 1")
    assert world.services.held.get(KEY) is not None
    assert phase_of(h) is ChamberPhase
    assert b"RESUMING IN CITY A" in ask(h, "/RESUME")
    assert ask(h, "/GO 0").endswith(b"\x05C\r")


def test_a_second_resume_is_not_possible_once_the_game_is_taken() -> None:
    world = make_world()
    drop_alice(world)
    h, _ = back(world)
    launch(h)
    assert world.services.held.get(KEY) is None


# --- the slot ----------------------------------------------------------------------------------


def test_the_team_slot_is_reserved_so_a_guest_asking_for_it_is_balanced_elsewhere() -> None:
    world = make_world()
    drop_alice(world, team=1)
    for _ in range(3):
        g = play(world, "A", 1)
        fly(g)
    late = login(world)
    late.feed(CSTTBT)
    out = ask(late, "/PLAY A BLUE")
    assert b"BLUE IS FULL" in out  # three players and ALICE's held seat


def test_the_resuming_player_takes_her_slot_though_the_team_is_full_of_others() -> None:
    world = make_world()
    drop_alice(world, team=1)
    for _ in range(3):
        g = play(world, "A", 1)
        fly(g)
    h, _ = back(world)
    assert ask(h, "/RESUME").endswith(b"\x05E 1 1\r")
    assert ask(h, "/GO 0").endswith(b"\x05C\r")
    member = world.lobby.find("ALICE")
    assert member is not None and (member.city, member.team) == ("A", 1)


def test_plain_play_finds_the_player_her_own_room_in_a_city_that_is_otherwise_full() -> None:
    world = make_world(city("A", capacity=1), default="A")
    drop_alice(world, team=0)
    guest = login(world)
    guest.feed(CSTTBT)
    assert b"ALL CITIES ARE FULL" in ask(guest, "/PLAY")
    h, _ = back(world)
    assert ask(h, "/PLAY").endswith(b"\x05E 1 1\r")


def test_the_world_is_kept_for_the_return_even_if_the_player_was_alone() -> None:
    world = make_world()
    a = play(world, "A", 1, ALICE)
    fly(a)
    city_a = world.services.cities["A"]
    city_a.orders.place_tower(1, WirePos.from_world(900, 20, 900), False, world.clock.now())
    a.session.closed()
    city_a.tick(world.clock.now())
    h, _ = back(world)
    launch(h)
    assert len(city_a.orders.towers) == 1
    assert [p.callsign for p in city_a.players()] == ["ALICE"]


# --- giving it up ------------------------------------------------------------------------------


def test_play_gives_the_held_game_up_and_starts_a_new_one() -> None:
    world = make_world()
    a = play(world, "A", 1, ALICE)
    fly(a)
    city_a = world.services.cities["A"]
    city_a.orders.place_tower(1, WirePos.from_world(900, 20, 900), False, world.clock.now())
    a.session.closed()
    h, _ = back(world)
    out = ask(h, "/PLAY B")
    assert b"YOUR HELD GAME IS GIVEN UP" in out and out.endswith(b"\x05E 2 2\r")
    ask(h, "/GO 1")  # the client cannot load map 2: back to the lobby, the game still given up
    assert world.services.held.get(KEY) is None
    assert world.services.held.slots("A") == []
    city_a.tick(world.clock.now())
    assert city_a.orders.towers == {}  # nothing is holding city A any more
    assert b"NO GAME IS HELD FOR YOU" in ask(h, "/RESUME")


def test_a_play_that_is_refused_keeps_the_held_game() -> None:
    world = make_world()
    drop_alice(world)
    h, _ = back(world)
    assert b"NO SUCH CITY" in ask(h, "/PLAY Z")
    assert world.services.held.get(KEY) is not None
