"""A slot held for a dropped player keeps the city's world alive and its room reserved (II §13 item
4, D9, II §17)."""

from __future__ import annotations

from fixtures.cities import Npc
from fixtures.drops import Held, game
from fixtures.lobbies import city
from opencs import policy
from opencs.messages import WirePos, client
from opencs.world.city import City
from opencs.world.seating import JoinRefused, JoinResult

TOWER = WirePos.from_world(900, 20, 900)


def alone(held: Held) -> tuple[City, int]:
    c, (a,) = game([0], reservations=held)
    c.orders.place_tower(0, TOWER, False, 2.0)
    return c, a


def test_a_held_slot_keeps_the_world_of_an_emptied_city() -> None:
    held = Held()
    c, a = alone(held)
    held.held = [("acct:P0", 0)]
    c.line_drop(a, 30.0, hold=True)
    c.tick(31.0)
    assert len(c.orders.towers) == 1 and c.players() == []


def test_without_a_hold_the_city_is_reset_when_it_empties() -> None:
    c, a = alone(Held())
    c.line_drop(a, 30.0)
    assert c.orders.towers == {}


def test_a_hold_that_is_not_eligible_does_not_keep_the_city() -> None:
    c, a = alone(Held())
    c.line_drop(a, 30.0, hold=True)  # eligible: the caller is about to reserve; kept for now
    assert len(c.orders.towers) == 1
    c.tick(31.0)  # but nothing was reserved by the next tick
    assert c.orders.towers == {}


def test_the_world_is_reset_on_the_first_tick_after_the_last_hold_goes() -> None:
    held = Held()
    c, a = alone(held)
    held.held = [("acct:P0", 0)]
    c.line_drop(a, 30.0, hold=True)
    c.tick(100.0)
    assert len(c.orders.towers) == 1
    held.held = []  # given up, or expired
    c.tick(101.0)
    assert c.orders.towers == {}


def test_a_death_at_the_drop_does_not_keep_the_city_even_with_hold_asked() -> None:
    held = Held()
    c, (a,) = game([0], reservations=held)
    c.orders.place_tower(0, TOWER, False, 2.0)
    c.spawn(Npc(4100), 0.0)
    c.client_message(a, client.Shot(4100, WirePos.from_world(2000, 20, 3000), 0, 0, 1), 5.0)
    dropped = c.line_drop(a, 6.0, hold=True)  # an NPC shot at the pod: a fight, so a death
    assert dropped.record is not None and not dropped.record.eligible
    assert c.orders.towers == {}  # reset at once: no later drop, no tick


def test_the_same_player_rejoins_the_same_world_not_a_new_game() -> None:
    held = Held()
    c, a = alone(held)
    held.held = [("acct:P0", 0)]
    c.line_drop(a, 30.0, hold=True)
    starts: list[float] = []
    c.sentinels.start = starts.append  # type: ignore[method-assign]
    again = c.join("P0", 0, 40.0, identity="acct:P0")
    assert isinstance(again, JoinResult)
    assert len(c.orders.towers) == 1
    assert starts == []  # the game's programs were not started over


def test_an_empty_world_that_is_kept_runs_its_ticks() -> None:
    held = Held()
    c, a = alone(held)
    held.held = [("acct:P0", 0)]
    c.line_drop(a, 30.0, hold=True)
    for i in range(1, 600):
        assert c.tick(30.0 + i) == []


# --- the room a held slot takes --------------------------------------------------------------


def held_city(held: Held, capacity: int = 16) -> City:
    return City(city("A", capacity=capacity), reservations=held)


def test_a_held_team_slot_counts_against_the_teams_four() -> None:
    held = Held()
    c = held_city(held)
    for i in range(3):
        assert isinstance(c.join(f"P{i}", 0, 0.0), JoinResult)
    held.held = [("acct:GONE", 0)]
    refused = c.join("P9", 0, 0.0, identity="acct:P9")
    assert isinstance(refused, JoinRefused)
    assert isinstance(c.join("P8", 1, 0.0), JoinResult)  # another team is open


def test_the_held_player_itself_may_take_its_slot() -> None:
    held = Held()
    c = held_city(held)
    for i in range(3):
        c.join(f"P{i}", 0, 0.0)
    held.held = [("acct:GONE", 0)]
    again = c.join("GONE", 0, 0.0, identity="acct:GONE")
    assert isinstance(again, JoinResult) and again.team == 0


def test_a_held_slot_counts_against_the_cities_capacity() -> None:
    held = Held()
    c = held_city(held, capacity=2)
    c.join("P0", 0, 0.0)
    held.held = [("acct:GONE", 1)]
    assert isinstance(c.join("P1", 2, 0.0, identity="acct:P1"), JoinRefused)
    assert isinstance(c.join("GONE", 1, 0.0, identity="acct:GONE"), JoinResult)


def test_slots_of_other_teams_do_not_close_this_team() -> None:
    held = Held()
    c = held_city(held)
    held.held = [("acct:X", 1), ("acct:Y", 2), ("acct:Z", 3)]
    assert isinstance(c.join("P0", 0, 0.0), JoinResult)
    assert policy.MAX_PLAYERS_PER_TEAM == 4
