"""Held slots occupy room in the lobby's capacity and team balancing, except for their owner
(II §13 item 4, D9)."""

from __future__ import annotations

import random
from collections.abc import Sequence

from fixtures.lobbies import add, city
from opencs.lobby.lobby import Denied, Lobby, Placement


class Held:
    def __init__(self) -> None:
        self.held: list[tuple[str, int, str]] = []  # (key, team, city)

    def slots(self, letter: str) -> Sequence[tuple[str, int]]:
        return [(k, t) for k, t, c in self.held if c == letter]


def lobby(held: Held, **kw: object) -> Lobby:
    return Lobby([city("A", **kw), city("B")], "B", random.Random(1), held)


def test_a_held_slot_fills_a_team_so_the_requested_team_is_refused_and_balanced() -> None:
    held = Held()
    lob = lobby(held)
    for n in range(3):
        add(lob, f"P{n}", "A", 0)
    held.held = [("acct:X", 0, "A")]  # the fourth seat of team 0 is held
    placed = lob.place("A", 0)
    assert isinstance(placed, Placement) and placed.team != 0
    assert "RED IS FULL" in placed.note


def test_the_owner_of_the_slot_may_take_it() -> None:
    held = Held()
    lob = lobby(held)
    for n in range(3):
        add(lob, f"P{n}", "A", 0)
    held.held = [("acct:X", 0, "A")]
    placed = lob.place("A", 0, exclude="acct:X")
    assert isinstance(placed, Placement) and placed.team == 0 and placed.note == ""


def test_balancing_counts_the_held_slot_of_a_team() -> None:
    held = Held()
    lob = lobby(held, balance_teams=(0, 1))
    add(lob, "P0", "A", 0)
    add(lob, "P1", "A", 1)
    held.held = [("acct:X", 0, "A")]  # team 0 is 2 with it, team 1 is 1
    placed = lob.place("A", None)
    assert isinstance(placed, Placement) and placed.team == 1
    placed = lob.place("A", None, exclude="acct:X")  # without its own seat team 0 is 1: a tie
    assert isinstance(placed, Placement) and placed.team == 0


def test_a_held_slot_counts_against_the_cities_capacity() -> None:
    held = Held()
    lob = lobby(held, capacity=2)
    add(lob, "P0", "A", 0)
    held.held = [("acct:X", 1, "A")]
    assert lob.place("A", None) == Denied("CITY A IS FULL")
    assert isinstance(lob.place("A", None, exclude="acct:X"), Placement)


def test_plain_play_skips_a_city_whose_only_room_is_held() -> None:
    held = Held()
    lob = lobby(held, capacity=1)
    held.held = [("acct:X", 0, "A")]
    placed = lob.place(None, None)
    assert isinstance(placed, Placement) and placed.city.letter == "B"


def test_slots_of_another_city_take_no_room_here() -> None:
    held = Held()
    lob = lobby(held, capacity=1)
    held.held = [("acct:X", 0, "B")]
    placed = lob.place("A", None)
    assert isinstance(placed, Placement) and placed.city.letter == "A"


def test_enter_refuses_a_seat_that_is_held_for_someone_else() -> None:
    held = Held()
    lob = lobby(held)
    for n in range(3):
        add(lob, f"P{n}", "A", 0)
    held.held = [("acct:X", 0, "A")]
    newcomer = lob.join("NEW", "NEW", lambda data: None)
    owner = lob.join("X", "X", lambda data: None)
    place = Placement(lob.cities()[0], 0)
    assert not lob.enter(newcomer, place)
    assert lob.enter(owner, place)


def test_the_cities_player_count_is_players_only() -> None:
    held = Held()
    lob = lobby(held)
    add(lob, "P0", "A", 0)
    held.held = [("acct:X", 0, "A")]
    assert lob.count("A") == 1
