"""Lobby state, city and team choice, chat relay (II §5, "Teams (S9)")."""

from __future__ import annotations

import random

from fixtures.lobbies import add, city, fill, make
from opencs.lobby.lobby import Denied, Member, Placement


def placed(result: Placement | Denied) -> Placement:
    assert isinstance(result, Placement), result
    return result


# --- members ---------------------------------------------------------------------------------


def test_join_leave_find_and_ordering() -> None:
    lobby = make()
    b, a = add(lobby, "BRAVO"), add(lobby, "ALPHA")
    assert [m.callsign for m in lobby.members()] == ["ALPHA", "BRAVO"]
    assert lobby.find("BRAVO") is b
    assert lobby.find("NOBODY") is None
    assert a.in_chamber
    lobby.leave(b)
    lobby.leave(b)  # leaving twice is harmless
    assert lobby.find("BRAVO") is None


def test_disabled_cities_are_not_offered() -> None:
    lobby = make(city("A"), city("B", enabled=False), default="A")
    assert [c.letter for c in lobby.cities()] == ["A"]
    assert lobby.city("B") is None
    assert isinstance(lobby.place("B", None), Denied)


# --- /PLAY: city -----------------------------------------------------------------------------


def test_plain_play_with_every_city_empty_goes_to_the_default() -> None:
    assert placed(make().place(None, None)).city.letter == "B"
    assert placed(make(default="C").place(None, None)).city.letter == "C"


def test_plain_play_picks_the_most_populated_city_with_room() -> None:
    lobby = make()
    fill(lobby, "A", 3)
    fill(lobby, "C", 5)
    assert placed(lobby.place(None, None)).city.letter == "C"


def test_plain_play_skips_a_full_city() -> None:
    lobby = make()
    fill(lobby, "C", 16)
    fill(lobby, "A", 2)
    assert placed(lobby.place(None, None)).city.letter == "A"
    fill(lobby, "A", 14, prefix="Q")
    fill(lobby, "B", 1)
    assert placed(lobby.place(None, None)).city.letter == "B"


def test_a_tie_between_populated_cities_prefers_the_default_then_the_lowest_letter() -> None:
    lobby = make()
    fill(lobby, "A", 2)
    fill(lobby, "B", 2)
    fill(lobby, "C", 2)
    assert placed(lobby.place(None, None)).city.letter == "B"
    lobby = make(default="B")
    fill(lobby, "C", 2)
    fill(lobby, "A", 2)
    assert placed(lobby.place(None, None)).city.letter == "A"


def test_plain_play_when_everything_is_full_is_denied() -> None:
    lobby = make(city("A"), default="A")
    fill(lobby, "A", 16)
    result = lobby.place(None, None)
    assert isinstance(result, Denied)
    assert "FULL" in result.reason


def test_play_with_a_letter_names_that_city_or_is_refused() -> None:
    lobby = make()
    assert placed(lobby.place("C", None)).city.letter == "C"
    unknown = lobby.place("Z", None)
    assert isinstance(unknown, Denied)
    assert "Z" in unknown.reason
    fill(lobby, "A", 16)
    full = lobby.place("A", None)
    assert isinstance(full, Denied)
    assert "FULL" in full.reason


def test_capacity_below_sixteen_is_honored() -> None:
    lobby = make(city("A", capacity=3), default="A")
    fill(lobby, "A", 2)
    assert isinstance(lobby.place("A", None), Placement)
    add(lobby, "LAST", "A", 2)
    assert isinstance(lobby.place("A", None), Denied)


# --- /PLAY: team -----------------------------------------------------------------------------


def test_a_requested_team_with_room_is_honored() -> None:
    result = placed(make().place("B", 2))
    assert (result.team, result.note) == (2, "")


def test_a_full_requested_team_is_balanced_onto_the_smallest_with_a_line() -> None:
    lobby = make()
    for i in range(4):
        add(lobby, f"G{i}", "B", 2)
    add(lobby, "R0", "B", 0)
    add(lobby, "R1", "B", 0)
    add(lobby, "K0", "B", 3)
    result = placed(lobby.place("B", 2))
    assert result.team == 1  # BLUE is empty
    assert result.note == "GREEN IS FULL: YOU ARE ON BLUE"


def test_no_request_balances_onto_the_smallest_team_lowest_number_on_ties() -> None:
    lobby = make()
    assert placed(lobby.place("B", None)).team == 0
    add(lobby, "R0", "B", 0)
    assert placed(lobby.place("B", None)).team == 1
    add(lobby, "R1", "B", 1)
    add(lobby, "G0", "B", 2)
    add(lobby, "K0", "B", 3)
    add(lobby, "K1", "B", 3)
    assert placed(lobby.place("B", None)).team == 0  # counts 1,1,1,2: first of the smallest
    add(lobby, "R2", "B", 0)
    assert placed(lobby.place("B", None)).team == 1  # counts 2,1,1,2
    assert placed(lobby.place("B", None)).note == ""


def test_the_balancer_only_uses_the_citys_balance_teams() -> None:
    lobby = make(city("B", balance_teams=(2,)))
    assert placed(lobby.place("B", None)).team == 2
    for i in range(4):
        add(lobby, f"G{i}", "B", 2)
    # Team 2 is full: fall back to any open team rather than refuse a city that has room.
    assert placed(lobby.place("B", None)).team == 0
    # A request is still honored outside the balance set when the policy allows requests.
    assert placed(lobby.place("B", 3)).team == 3


def test_balance_policy_ignores_the_request_and_says_so() -> None:
    lobby = make(city("B", team_policy="balance"))
    result = placed(lobby.place("B", 3))
    assert result.team == 0
    assert result.note == "CITY B CHOOSES TEAMS ITSELF: YOU ARE ON RED"


def test_random_policy_draws_from_the_injected_generator() -> None:
    def draws(seed: int) -> list[int]:
        lobby = make(city("B", team_policy="random"), seed=seed)
        return [placed(lobby.place("B", None)).team for _ in range(12)]

    assert draws(5) == draws(5)  # deterministic for a seed
    assert draws(5) != draws(6)
    expected = random.Random(5)
    assert draws(5) == [expected.choice([0, 1, 2, 3]) for _ in range(12)]


def test_random_policy_only_picks_open_teams() -> None:
    lobby = make(city("B", team_policy="random"), seed=3)
    for team in (0, 1, 2):
        for i in range(4):
            add(lobby, f"T{team}{i}", "B", team)
    assert all(placed(lobby.place("B", None)).team == 3 for _ in range(20))


# --- entering --------------------------------------------------------------------------------


def test_enter_records_location_and_refuses_a_place_that_filled() -> None:
    lobby = make()
    me = add(lobby, "ME")
    plan = placed(lobby.place("B", 1))
    assert lobby.enter(me, plan)
    assert (me.city, me.team, me.in_chamber) == ("B", 1, False)
    assert lobby.count("B") == 1
    assert [m.callsign for m in lobby.team_members("B", 1)] == ["ME"]
    other = add(lobby, "OTHER")
    for i in range(4):
        add(lobby, f"F{i}", "B", 0)
    assert not lobby.enter(other, Placement(plan.city, 0))
    assert other.in_chamber


# --- chat ------------------------------------------------------------------------------------


def test_chat_goes_to_other_chamber_members_only_and_is_not_echoed() -> None:
    lobby = make()
    got: dict[str, list[bytes]] = {}

    def member(name: str, letter: str | None = None) -> Member:
        m = lobby.join(name, None, lambda data, n=name: got.setdefault(n, []).append(data))  # type: ignore[misc]
        if letter:
            m.city, m.team = letter, 0
        return m

    sender, listener, _, _ = (
        member("SENDER"),
        member("LISTENER"),
        member("PLAYER", "B"),
        member("X"),
    )
    assert lobby.relay_chat(sender, "hello") == 2
    assert "SENDER" not in got
    assert "PLAYER" not in got  # in a game
    assert got["LISTENER"] == [b"\x05D4\rSENDER: hello\r\x05D0\r"]
    assert got["X"] == got["LISTENER"]
    assert listener.in_chamber


def test_chat_is_sanitized_and_cut_to_72_characters() -> None:
    lobby = make()
    got: list[bytes] = []
    sender = add(lobby, "SENDER")
    lobby.join("LISTENER", None, got.append)
    lobby.relay_chat(sender, "a\x01b\x05c\x07d\x1b\x7fe\xe9f")
    assert got[-1] == b"\x05D4\rSENDER: abcdef\r\x05D0\r"
    lobby.relay_chat(sender, "x" * 100)
    assert got[-1] == b"\x05D4\rSENDER: " + b"x" * 72 + b"\r\x05D0\r"
    lobby.relay_chat(sender, "y" * 72)
    assert got[-1].count(b"y") == 72


def test_chat_cannot_arm_command_mode_on_the_receiver() -> None:
    lobby = make()
    got: list[bytes] = []
    sender = add(lobby, "SENDER")
    lobby.join("LISTENER", None, got.append)
    lobby.relay_chat(sender, "\x05E 1 1\r\x05C\r\x01")
    # The only arming bytes are the server's own color commands.
    assert got[-1].count(b"\x05") == 2
    assert b"\x01" not in got[-1]
    assert got[-1].count(b"\r") == 3


def test_blank_chat_after_sanitizing_is_not_relayed() -> None:
    lobby = make()
    got: list[bytes] = []
    sender = add(lobby, "SENDER")
    lobby.join("LISTENER", None, got.append)
    assert lobby.relay_chat(sender, "\x01\x05") == 0
    assert lobby.relay_chat(sender, "   ") == 0
    assert got == []
