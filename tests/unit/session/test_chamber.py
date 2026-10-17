"""The Chamber through a session: version check, every row of the II §5, the launch."""

from __future__ import annotations

from fixtures.lobbies import add, city, fill
from fixtures.sessions import Harness, World, enter_lobby, login, make_world
from opencs import policy
from opencs.session.chamber import ChamberPhase
from opencs.session.ingame import InGamePhase
from opencs.session.login import LoginPhase
from opencs.store.base import ProgressRecord
from opencs.store.memory import MemoryProgressStore
from opencs.wire.frame import build_frame

PRESS_RETURN = b"\x05PRESS RETURN\r"
ENTER_A = b"\x05A\r"


def ask(h: Harness, line: str) -> bytes:
    """Send one line and return what came back."""
    h.take()
    h.feed(line + "\r")
    return h.take()


def phase_of(h: Harness) -> type:
    return type(h.session.phase)


# --- entering --------------------------------------------------------------------------------


def test_entering_asks_press_return_then_a_and_a_welcome_after_the_version_reply() -> None:
    h = login(make_world())
    assert h.take().endswith(PRESS_RETURN)
    h.feed("CSTTBT 105 150\r")
    out = h.take()
    assert out.startswith(ENTER_A)
    assert (
        out[len(ENTER_A) :]
        == b"\x05D2\rWELCOME TO THE CHAMBER, JOINGENI. TYPE /HELP FOR COMMANDS.\r\x05D0\r"
    )


def test_a_bare_cr_is_an_acceptable_version_reply() -> None:
    h = login(make_world())
    h.take()
    h.feed("\r")
    assert h.take().startswith(ENTER_A)


def test_any_reply_is_accepted_and_never_refused() -> None:
    h = login(make_world())
    h.take()
    h.feed("CSTTBT 999 999\r")
    assert h.take().startswith(ENTER_A)
    assert not h.io.closed


def test_no_reply_within_five_seconds_enters_anyway() -> None:
    h = login(make_world())
    h.take()
    h.advance(policy.VERSION_WAIT - 0.1)
    assert h.take() == b""
    h.advance(0.2)
    assert h.take().startswith(ENTER_A)
    # A late reply is neither a second entry nor chat (see the two-player test below).
    h.feed("CSTTBT 105 150\r")
    assert ENTER_A not in h.take()


def test_a_late_version_reply_is_not_relayed_as_chat() -> None:
    world = make_world()
    other = enter_lobby(world, "OTHER")
    h = login(world)
    h.take()
    h.advance(policy.VERSION_WAIT)  # exactly the boundary
    assert h.take().startswith(ENTER_A)
    other.take()
    h.feed("CSTTBT 105 150\r")
    assert other.take() == b""
    # Only once: the same text later is ordinary chat.
    h.feed("CSTTBT 105 150\r")
    assert b"CSTTBT 105 150" in other.take()


def test_a_non_reply_first_line_after_the_timeout_is_still_chat() -> None:
    world = make_world()
    other = enter_lobby(world, "OTHER")
    h = login(world)
    h.advance(policy.VERSION_WAIT)
    other.take()
    h.feed("hello\r")
    assert b"hello" in other.take()


# --- the line rate limit (DESIGN §5) -----------------------------------------------------------


def test_a_burst_of_chat_is_cut_to_the_budget_and_the_sender_is_warned_once() -> None:
    world = make_world()
    other = enter_lobby(world, "OTHER")
    h = enter_lobby(world)
    other.take()
    h.feed("x\r" * (policy.CHAMBER_BURST_LINES + 5))
    assert other.take().count(b"JOINGENI: x") == policy.CHAMBER_BURST_LINES
    assert h.take().count(b"TOO MANY LINES") == 1


def test_the_budget_refills_with_time() -> None:
    world = make_world()
    other = enter_lobby(world, "OTHER")
    h = enter_lobby(world)
    h.feed("x\r" * (policy.CHAMBER_BURST_LINES + 5))
    h.take()
    other.take()
    h.advance(1 / policy.CHAMBER_LINES_PER_SECOND)
    h.feed("y\r")
    assert b"JOINGENI: y" in other.take()
    assert b"TOO MANY" not in h.take()


def test_sustained_flooding_closes_the_connection() -> None:
    h = enter_lobby(make_world())
    h.feed("x\r" * (policy.CHAMBER_BURST_LINES + policy.CHAMBER_FLOOD_DROPS + 1))
    assert h.io.closed


def test_honest_pace_is_never_limited() -> None:
    h = enter_lobby(make_world())
    for _ in range(100):
        h.advance(1.0)
        h.feed("/STATUS\r")
    assert not h.io.closed
    assert b"TOO MANY" not in h.take()


def test_the_go_reply_is_not_rate_limited() -> None:
    h = enter_lobby(make_world())
    h.feed("/PLAY A\r")
    h.take()
    h.feed("x\r" * (policy.CHAMBER_BURST_LINES + 5))  # in LAUNCH: not chat, not counted
    h.take()
    h.feed("/GO 0\r")
    assert h.take() == b"\x05C\r"


def test_a_reply_before_the_timeout_does_not_enter_twice() -> None:
    h = login(make_world())
    h.take()
    h.feed("\r")
    h.take()
    h.advance(policy.VERSION_WAIT + 1)
    assert h.take() == b""


def test_the_pilot_is_in_the_lobby_from_entering() -> None:
    world = make_world()
    login(world)
    assert world.lobby.find("JOINGENI") is not None


# --- /HELP /CITY /STATUS ... -----------------------------------------------------------------


def test_help_lists_commands_and_sends_a_again() -> None:
    h = enter_lobby(make_world())
    out = ask(h, "/HELP")
    assert out.endswith(b"\x05A\r")
    for word in (b"/CITY", b"/PLAY", b"/STATUS", b"/WHO", b"/SCORE", b"/RESUME", b"/QUIT"):
        assert word in out
    assert ask(h, "/help").endswith(b"\x05A\r")


def test_bare_city_is_plain_text_one_line_per_enabled_city() -> None:
    world = make_world(city("A"), city("B"), city("C", enabled=False), default="B")
    h = enter_lobby(world)
    add(world.lobby, "OTHER", "B", 0)
    out = ask(h, "/CITY")
    assert b"\x01" not in out
    assert b"\x05" not in out
    rows = out.decode().split("\r")[:-1]
    assert [r.split()[0] for r in rows] == ["A", "B"]
    assert rows[1].endswith("1/16")


def test_a_line_ending_in_city_is_a_bare_city_and_the_draft_is_not_relayed() -> None:
    world = make_world()
    h = enter_lobby(world)
    other = enter_lobby(world, "OTHER")
    out = ask(h, "/PLAY /CITY")
    assert out.startswith(b"NOT SENT: /PLAY\r")
    assert b"\x05" not in out
    assert other.take() == b""
    assert ask(h, "draft/CITY").startswith(b"NOT SENT: draft\r")
    assert ask(h, "/city").startswith(b"A  ")  # a bare one has no NOT SENT line


def test_a_bare_letter_after_a_bare_city_gets_the_hint_within_two_minutes() -> None:
    world = make_world()
    h = enter_lobby(world)
    other = enter_lobby(world, "OTHER")
    ask(h, "/CITY")
    h.advance(policy.CITY_HINT_WINDOW - 1)
    out = ask(h, "b")
    assert b"TYPE /CITY B FOR INFORMATION OR /PLAY B TO PLAY" in out
    assert other.take() == b""  # the hint is not chat
    h.advance(1)  # exactly 2 minutes after the bare /CITY: still inside
    assert b"TYPE /CITY B" in ask(h, "b")
    h.advance(0.01)
    assert ask(h, "b") == b""  # window over: it is chat now
    assert other.take() == b"\x05D4\rJOINGENI: b\r\x05D0\r"


def test_a_bare_letter_with_no_prior_city_list_is_chat() -> None:
    world = make_world()
    h = enter_lobby(world)
    other = enter_lobby(world, "OTHER")
    assert ask(h, "C") == b""
    assert b"JOINGENI: C" in other.take()


def test_a_bare_letter_that_is_not_a_city_is_chat_even_after_a_city_list() -> None:
    world = make_world()
    h = enter_lobby(world)
    other = enter_lobby(world, "OTHER")
    ask(h, "/CITY")
    assert ask(h, "z") == b""
    assert b"JOINGENI: z" in other.take()


def test_city_with_a_letter_shows_teams_counts_and_callsigns() -> None:
    world = make_world()
    h = enter_lobby(world)
    add(world.lobby, "ZED", "B", 1)
    add(world.lobby, "ABE", "B", 1)
    out = ask(h, "/CITY B")
    assert out.startswith(b"\x05D6\r")
    assert b"BLUE  2/4  ABE ZED" in out
    assert b"RED   0/4  -" in out
    assert out.endswith(b"\x05D0\r")
    assert b"NO SUCH CITY: Z" in ask(h, "/CITY Z")


def test_status_and_who_list_every_pilot_and_where() -> None:
    world = make_world()
    h = enter_lobby(world)
    add(world.lobby, "ALPHA", "C", 3)
    for line in ("/STATUS", "/WHO", "/status"):
        out = ask(h, line)
        assert b"PILOTS ONLINE: 2" in out
        assert b"ALPHA     C BLACK" in out
        assert b"JOINGENI  CHAMBER" in out


def test_who_callsign_gives_location_and_the_record() -> None:
    world = make_world()
    h = enter_lobby(world)
    other = add(world.lobby, "ALPHA", "A", 0)
    out = ask(h, "/WHO alpha")
    assert b"ALPHA: A RED" in out and b"NO RECORD YET" in out
    world.progress.save(other.key, ProgressRecord((3, 2, 1, 1, 5, 4, 0, 1, 2), -2, 957, 1670, 500))
    out = ask(h, "/WHO alpha")
    for expected in (b"LAST GAME SCORE: 1670", b"CAREER TOTAL: 500", b"BAY POINTS: 957"):
        assert expected in out
    assert b"BAYS: 3 2 1 1 5 4 0 1 2" in out and b"BATTLE MARKER: -2" in out
    assert b"NO SUCH PILOT: NOBODY" in ask(h, "/WHO nobody")


def test_who_callsign_reads_the_record_of_that_pilot_not_the_askers() -> None:
    world = make_world()
    h = enter_lobby(world)
    other = add(world.lobby, "ALPHA", "A", 0)
    assert h.session.member is not None
    world.progress.save(h.session.member.key, ProgressRecord((1,) * 9, 0, 11, 22, 33))
    world.progress.save(other.key, ProgressRecord((2,) * 9, 0, 44, 55, 66))
    out = ask(h, "/WHO alpha")
    assert b"SCORE: 55" in out and b"SCORE: 22" not in out


def test_score_shows_the_players_own_record_for_a_guest_and_for_an_account() -> None:
    world = make_world()
    guest = enter_lobby(world)
    assert b"NO RECORD YET" in ask(guest, "/SCORE")
    world.progress.save("JOINGENI", ProgressRecord((5,) * 9, -7, 123, 4567, 89))
    out = ask(guest, "/SCORE")
    for expected in (b"RECORD OF JOINGENI", b"LAST GAME SCORE: 4567", b"CAREER TOTAL: 89"):
        assert expected in out
    assert b"BAY POINTS: 123" in out and b"BAYS: 5 5 5 5 5 5 5 5 5" in out
    assert b"BATTLE MARKER: -7" in out
    account = enter_lobby(world, "ALICE,pw")
    world.progress.save("acct:ALICE", ProgressRecord((4,) * 9, 3, 1, 2, 3))
    assert b"BATTLE MARKER: 3" in ask(account, "/SCORE")


def test_an_unreadable_record_is_reported_and_the_session_carries_on() -> None:
    class Broken(MemoryProgressStore):
        def load(self, identity: str) -> ProgressRecord | None:
            raise OSError("gone")

    world = make_world(progress=Broken())
    h = enter_lobby(world)
    assert b"CANNOT BE READ" in ask(h, "/SCORE")
    assert b"CANNOT BE READ" in ask(h, "/WHO joingeni")
    assert b"PILOTS ONLINE" in ask(h, "/STATUS")


def test_resume_is_refused_for_joingenie_and_for_an_account_with_no_held_game() -> None:
    world = make_world()
    guest = enter_lobby(world)
    assert b"JOINGENIE LOGINS CANNOT RESUME" in ask(guest, "/RESUME")
    real = enter_lobby(world, "ALPHA,pw")
    out = ask(real, "/RESUME")
    assert b"NO GAME IS HELD FOR YOU" in out
    assert b"JOINGENIE" not in out
    assert phase_of(real) is ChamberPhase


def test_colors_shows_the_seven_colors() -> None:
    out = ask(enter_lobby(make_world()), "/COLORS")
    for n in range(7):
        assert f"\x05D{n}\rD{n}  THIS IS COLOR {n}\r".encode() in out
    assert b"D7" not in out


def test_unknown_command_gets_a_line() -> None:
    h = enter_lobby(make_world())
    assert ask(h, "/dance") == b"\x05D3\rUNKNOWN COMMAND: /DANCE. TYPE /HELP\r\x05D0\r"


def test_bad_arguments_get_a_usage_line() -> None:
    h = enter_lobby(make_world())
    assert b"USAGE: /PLAY" in ask(h, "/PLAY B PURPLE")
    assert b"USAGE: /CITY" in ask(h, "/CITY 7")


def test_blank_lines_do_nothing() -> None:
    h = enter_lobby(make_world())
    assert ask(h, "") == b""
    assert ask(h, "   ") == b""


def test_an_overlong_line_is_refused_not_relayed() -> None:
    world = make_world()
    h = enter_lobby(world)
    other = enter_lobby(world, "OTHER")
    h.feed(b"x" * (policy.MAX_LINE_BYTES + 5) + b"\r")
    assert b"TOO LONG" in h.take()
    assert other.take() == b""
    assert not h.io.closed


# --- chat ------------------------------------------------------------------------------------


def test_a_plain_line_reaches_the_other_chamber_players_but_not_the_sender() -> None:
    world = make_world()
    a = enter_lobby(world, "ALPHA,pw")
    b = enter_lobby(world, "BRAVO,pw")
    c = enter_lobby(world, "CHARLIE,pw")
    add(world.lobby, "GAMER", "B", 0)  # in a game: gets nothing, and has no session to check
    a.feed("hello \x01there\x05\r")
    assert a.take() == b""
    expected = b"\x05D4\rALPHA: hello there\r\x05D0\r"
    assert b.take() == expected
    assert c.take() == expected


def test_chat_does_not_reach_a_pilot_at_the_genie_prompt() -> None:
    world = make_world()
    a = enter_lobby(world, "ALPHA,pw")
    b = enter_lobby(world, "BRAVO,pw")
    b.feed("/QUIT\r")
    b.take()
    a.feed("anyone there\r")
    assert b.take() == b""


# --- /PLAY and the launch --------------------------------------------------------------------


def go(h: Harness, line: str = "/PLAY") -> bytes:
    return ask(h, line)


def test_play_with_every_city_empty_launches_the_default_city_b_map_2() -> None:
    h = enter_lobby(make_world())
    assert go(h) == b"\x05E 2 2\r"


def test_play_chooses_the_most_populated_city_with_room() -> None:
    world = make_world()
    fill(world.lobby, "C", 5)
    fill(world.lobby, "A", 2)
    assert go(enter_lobby(world)) == b"\x05E 3 3\r"


def test_play_skips_a_full_city() -> None:
    world = make_world()
    fill(world.lobby, "C", 16)
    fill(world.lobby, "A", 2)
    assert go(enter_lobby(world)) == b"\x05E 1 1\r"


def test_play_letter_and_team() -> None:
    world = make_world()
    h = enter_lobby(world)
    assert go(h, "/PLAY a green") == b"\x05E 1 1\r"
    h.feed("/GO 0\r")
    assert world.lobby.find("JOINGENI").team == 2  # type: ignore[union-attr]


def test_a_full_team_is_balanced_with_a_line_before_the_launch() -> None:
    world = make_world()
    for i in range(4):
        add(world.lobby, f"G{i}", "A", 2)
    h = enter_lobby(world)
    out = go(h, "/PLAY A GREEN")
    assert out == b"\x05D2\rGREEN IS FULL: YOU ARE ON RED\r\x05D0\r\x05E 1 1\r"


def test_play_in_a_city_that_does_not_exist_or_is_full_stays_in_the_chamber() -> None:
    world = make_world()
    fill(world.lobby, "A", 16)
    h = enter_lobby(world)
    assert b"NO SUCH CITY: Z" in go(h, "/PLAY Z")
    assert b"CITY A IS FULL" in go(h, "/PLAY A")
    assert phase_of(h) is ChamberPhase


def test_the_launch_flow_e_then_go_0_then_c_and_the_phase_switch() -> None:
    world = make_world()
    h = enter_lobby(world)
    assert go(h, "/PLAY B") == b"\x05E 2 2\r"
    assert phase_of(h) is ChamberPhase
    h.feed("/GO 0\r")
    assert h.take() == b"\x05C\r"
    assert phase_of(h) is InGamePhase
    member = world.lobby.find("JOINGENI")
    assert member is not None
    assert (member.city, member.team) == ("B", 0)
    assert not h.io.closed


def test_binary_bytes_in_the_same_feed_as_go_0_go_to_the_in_game_phase() -> None:
    world = make_world()
    h = enter_lobby(world)
    go(h, "/PLAY")
    ping = build_frame(b"EN", terminator=b"\x0a")
    # The client may start sending the moment it sees C; bytes after the /GO line are binary and
    # contain 0x0d and bytes >= 0x80 that a line reader would mangle.
    h.feed(b"/GO 0\r" + ping + build_frame(b"d\x10\x11\x12", terminator=b"\x0a"))
    phase = h.session.phase
    assert isinstance(phase, InGamePhase)
    assert phase._reader.frames == 2
    assert phase._bad.total == 0


def test_go_arriving_in_pieces_still_launches() -> None:
    h = enter_lobby(make_world())
    go(h)
    for piece in (b"/G", b"O ", b"0", b"\r"):
        h.feed(piece)
    assert h.take() == b"\x05C\r"


def test_go_1_2_3_name_the_problem_and_stay_in_the_chamber() -> None:
    for status, text in ((1, b"MISSING"), (2, b"DOES NOT MATCH"), (3, b"MEMORY")):
        world = make_world()
        h = enter_lobby(world)
        go(h)
        out = ask(h, f"/GO {status}")
        assert text in out and b"MAP 2" in out
        assert b"\x05C\r" not in out
        assert phase_of(h) is ChamberPhase
        assert world.lobby.find("JOINGENI").in_chamber  # type: ignore[union-attr]
        assert go(h) == b"\x05E 2 2\r"  # and the player may try again


def test_an_unknown_go_status_stays() -> None:
    h = enter_lobby(make_world())
    go(h)
    assert b"UNKNOWN LAUNCH STATUS 9" in ask(h, "/GO 9")
    assert phase_of(h) is ChamberPhase


def test_go_that_never_comes_times_out_and_stays_in_the_chamber() -> None:
    h = enter_lobby(make_world())
    go(h)
    h.advance(policy.GO_WAIT - 0.1)
    assert h.take() == b""
    h.advance(0.2)
    assert b"NO REPLY TO THE LAUNCH REQUEST" in h.take()
    assert phase_of(h) is ChamberPhase
    # A late /GO 0 no longer launches.
    assert ask(h, "/GO 0") == b""
    assert phase_of(h) is ChamberPhase
    assert go(h) == b"\x05E 2 2\r"


def test_other_lines_while_waiting_for_go_are_refused_and_not_relayed() -> None:
    world = make_world()
    h = enter_lobby(world)
    other = enter_lobby(world, "OTHER")
    go(h)
    assert b"LAUNCH IN PROGRESS" in ask(h, "hello")
    assert b"LAUNCH IN PROGRESS" in ask(h, "/PLAY A")
    assert other.take() == b""
    h.feed("/GO 0\r")
    assert phase_of(h) is InGamePhase


def test_go_0_when_the_place_filled_meanwhile_stays_in_the_chamber() -> None:
    world = make_world(city("A", capacity=2), default="A")
    h = enter_lobby(world)
    go(h, "/PLAY A")
    add(world.lobby, "X1", "A", 0)
    add(world.lobby, "X2", "A", 1)
    out = ask(h, "/GO 0")
    assert b"CITY A FILLED UP" in out
    assert b"\x05C\r" not in out
    assert phase_of(h) is ChamberPhase


def test_a_second_player_joins_the_first_players_city() -> None:
    world = make_world()
    a = enter_lobby(world, "ALPHA,pw")
    go(a, "/PLAY A")
    a.feed("/GO 0\r")
    b = enter_lobby(world, "BRAVO,pw")
    assert go(b) == b"\x05E 1 1\r"  # A is the most populated city with room


def test_disconnecting_in_the_chamber_or_in_a_game_frees_the_pilot() -> None:
    world = make_world()
    a = enter_lobby(world, "ALPHA,pw")
    b = enter_lobby(world, "BRAVO,pw")
    go(b)
    b.feed("/GO 0\r")
    assert world.lobby.count("B") == 1
    a.session.closed()
    b.session.closed()
    assert world.lobby.members() == []
    assert world.lobby.count("B") == 0
    assert not world.identity.callsign_online("ALPHA")
    assert not world.identity.callsign_online("BRAVO")


# --- /QUIT -----------------------------------------------------------------------------------


def test_quit_sends_b_and_the_star_prompt_then_m_1380_re_enters_and_bye_closes() -> None:
    world = make_world()
    h = enter_lobby(world)
    out = ask(h, "/QUIT")
    assert out == b"\x05B\r\r\n*"
    assert phase_of(h) is LoginPhase
    assert world.lobby.find("JOINGENI") is None  # gone from the roster
    assert world.identity.callsign_online("JOINGENI")  # but still logged in
    assert ask(h, "M 1380;3") == PRESS_RETURN
    assert phase_of(h) is ChamberPhase
    h.feed("\r")
    h.take()
    assert world.lobby.find("JOINGENI") is not None
    ask(h, "/QUIT")
    assert ask(h, "BYE").endswith(b"GOODBYE\r")
    assert h.io.closed_with == ["BYE"]


def test_quit_in_the_middle_of_a_feed_hands_the_rest_to_the_login_phase() -> None:
    h = enter_lobby(make_world())
    h.feed("/QUIT\rM 1380;3\r")
    assert h.take() == b"\x05B\r\r\n*" + PRESS_RETURN


def test_go_0_then_a_quit_frame_in_one_feed_is_not_parsed_as_text() -> None:
    world: World = make_world()
    h = enter_lobby(world)
    go(h)
    h.feed(b"/GO 0\r" + b"QUIT\rQUIT\r")
    assert phase_of(h) is InGamePhase
    assert not h.io.closed
