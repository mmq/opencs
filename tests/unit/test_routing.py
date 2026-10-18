"""The router: each audience reaches exactly its players (I App. B)."""

from __future__ import annotations

from dataclasses import dataclass, field

from fixtures.lobbies import city
from opencs.messages import ServerMessage, server
from opencs.routing import Router
from opencs.world.city import City
from opencs.world.events import Event, ToAll, ToAllExcept, ToHolders, ToPlayer, ToPlayers, ToTeam
from opencs.world.seating import JoinResult


@dataclass
class Recorder:
    got: list[ServerMessage] = field(default_factory=list)

    def send(self, msg: ServerMessage) -> None:
        self.got.append(msg)


@dataclass
class Table:
    city: City
    router: Router
    sinks: dict[int, Recorder]


def table() -> Table:
    """Players 0 and 1 on team 0, 2 on team 1, 3 on team 2; player 3 also holds pod 2."""
    c = City(city("A"))
    router = Router()
    sinks = {}
    for callsign, team in (("A", 0), ("B", 0), ("C", 1), ("D", 2)):
        result = c.join(callsign, team, 0.0)
        assert isinstance(result, JoinResult)
        sinks[result.player_id] = Recorder()
        router.register("A", result.player_id, sinks[result.player_id])
    player = c.player(3)
    assert player is not None
    player.view.hold(2)
    return Table(c, router, sinks)


MSG = server.ReleaseDrop(2)


def who(t: Table, audience: object) -> list[int]:
    t.router.deliver(t.city, [Event(MSG, audience)])  # type: ignore[arg-type]
    hit = [pid for pid, sink in t.sinks.items() if sink.got]
    for sink in t.sinks.values():
        sink.got.clear()
    return hit


def test_to_player_reaches_only_that_player() -> None:
    assert who(table(), ToPlayer(1)) == [1]


def test_to_players_reaches_exactly_the_named_players_in_id_order() -> None:
    t = table()
    t.router.deliver(t.city, [Event(server.QuitAck(), ToPlayers(frozenset({3, 0, 9})))])
    assert [i for i, s in sorted(t.sinks.items()) if s.got] == [0, 3]


def test_to_all_reaches_everyone() -> None:
    assert who(table(), ToAll()) == [0, 1, 2, 3]


def test_to_all_except_skips_exactly_one() -> None:
    assert who(table(), ToAllExcept(2)) == [0, 1, 3]


def test_to_team_reaches_only_that_team() -> None:
    t = table()
    assert who(t, ToTeam(0)) == [0, 1]
    assert who(t, ToTeam(3)) == []


def test_to_holders_reaches_those_whose_view_holds_the_entity() -> None:
    t = table()
    assert who(t, ToHolders(2)) == [2, 3]  # its owner and the client that was introduced
    assert who(t, ToHolders(0)) == [0]
    assert who(t, ToHolders(200)) == []


def test_to_holders_except_leaves_out_one_holder() -> None:
    t = table()
    assert who(t, ToHolders(2, except_player=2)) == [3]  # the owner is left out
    assert who(t, ToHolders(2, except_player=3)) == [2]
    assert who(t, ToHolders(2, except_player=0)) == [2, 3]  # not a holder: changes nothing


def test_to_a_player_who_is_not_in_the_city_nobody_is_reached() -> None:
    assert who(table(), ToPlayer(9)) == []


def test_unregistered_players_are_skipped_and_the_rest_still_receive() -> None:
    t = table()
    t.router.unregister("A", 1)
    assert who(t, ToAll()) == [0, 2, 3]


def test_a_sink_registered_for_another_city_is_not_reached() -> None:
    t = table()
    other = Recorder()
    t.router.register("B", 0, other)
    who(t, ToAll())
    assert other.got == []


def test_a_failing_sink_does_not_stop_the_others() -> None:
    t = table()

    class Broken:
        def send(self, msg: ServerMessage) -> None:
            raise RuntimeError("boom")

    t.router.register("A", 0, Broken())
    assert who(t, ToAll()) == [1, 2, 3]


def test_events_are_delivered_in_order() -> None:
    t = table()
    first, second = server.ReleaseDrop(0), server.Destroy(0)
    t.router.deliver(t.city, [Event(first, ToPlayer(0)), Event(second, ToPlayer(0))])
    assert t.sinks[0].got == [first, second]
