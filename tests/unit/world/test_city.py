"""City: joining, the 'B', (team, index) slots, leaving and the pod handshake (I §7.1, I §6.3)."""

from __future__ import annotations

import pytest

from fixtures.lobbies import city
from fixtures.pods import report
from opencs.messages import ClientMessage, client, server, server_payload
from opencs.wire.frame import build_frame
from opencs.world.city import City, JoinRefused, JoinResult
from opencs.world.events import Event, ToAll, ToHolders, ToPlayer

# I App. A.1: the first frame of a real session (city "CYBERCITY ONE"), tag 11.
A3_B_FRAME = bytes.fromhex(
    "43534eb13b42111010184a4f494e47454e491547454e49451d435942455243495459204f4e4518111210100d"
)


def cyber_city_one() -> City:
    return City(city("A", name="CYBERCITY ONE", map=1, max_multiplier=8, tower_output=1))


def joined(c: City, callsign: str = "JOINGENI", team: int = 0, now: float = 0.0) -> JoinResult:
    result = c.join(callsign, team, now)
    assert isinstance(result, JoinResult)
    return result


def test_the_first_player_gets_id_0_slot_0_and_the_b_of_the_worked_session() -> None:
    result = joined(cyber_city_one())
    assert (result.player_id, result.team, result.index) == (0, 0, 0)
    assert build_frame(server_payload(result.hello, 0x11)) == A3_B_FRAME


def test_b_carries_the_city_settings_and_the_slot_of_a_later_player() -> None:
    c = City(city("B", name="TWO", max_multiplier=20, tower_output=2, max_module_level=4))
    joined(c, "AAA", 1)
    result = joined(c, "BBB", 1)
    hello = result.hello
    assert (hello.self_id, hello.index, hello.team) == (1, 1, 1)
    assert hello.callsign == b"BBB" and hello.network == b"GENIE" and hello.city == b"TWO"
    assert (hello.max_mult, hello.tower_output, hello.max_module_level) == (20, 2, 4)
    assert (hello.n4, hello.n5) == (0, 0)


def test_the_index_is_the_lowest_free_one_in_the_team() -> None:
    c = cyber_city_one()
    ids = [joined(c, f"P{i}", 2).player_id for i in range(3)]
    assert [c.player(i).index for i in ids] == [0, 1, 2]  # type: ignore[union-attr]
    c.leave(ids[1], 5.0)
    assert joined(c, "NEW", 2, now=6.0).index == 1  # the gap, not 3
    assert joined(c, "OTHER", 3).index == 0  # another team counts on its own


def test_a_team_holds_four_and_the_fifth_is_refused() -> None:
    c = cyber_city_one()
    for i in range(4):
        joined(c, f"P{i}", 1)
    refused = c.join("P4", 1, 0.0)
    assert isinstance(refused, JoinRefused)
    assert isinstance(c.join("P4", 0, 0.0), JoinResult)  # another team still has room


def test_a_city_refuses_beyond_its_capacity() -> None:
    c = City(city("A", capacity=2))
    joined(c, "A1", 0)
    joined(c, "A2", 1)
    assert isinstance(c.join("A3", 2, 0.0), JoinRefused)


def test_ids_are_unique_and_a_player_holds_its_own_pod() -> None:
    c = cyber_city_one()
    a, b = joined(c, "A", 0), joined(c, "B", 0)
    assert (a.player_id, b.player_id) == (0, 1)
    assert c.player(0).view.holds(0)  # type: ignore[union-attr]
    assert not c.player(0).view.holds(1)  # type: ignore[union-attr]


def handshake(c: City, pid: int, msg: ClientMessage, now: float) -> list[Event]:
    """What the city says about the pod's life, less the relay of its 'b'."""
    events = c.client_message(pid, msg, now)
    return [e for e in events if not isinstance(e.message, server.PodState)]


def test_the_drop_handshake_through_the_city() -> None:
    c = cyber_city_one()
    pid = joined(c).player_id
    release = Event(server.ReleaseDrop(pid), ToHolders(pid))
    # The first report: nobody else holds the pod yet, so the release is its owner's alone.
    assert handshake(c, pid, report(4), 1.0) == [Event(server.ReleaseDrop(pid), ToPlayer(pid))]
    assert c.tick(1.2) == [release]
    assert handshake(c, pid, report(1), 1.5) == []
    assert c.tick(1.6) == []
    assert handshake(c, pid, client.Died(), 30.0) == [Event(server.Destroy(pid), ToAll())]
    assert handshake(c, pid, report(4), 31.0) == []  # the fly-over: no answer, and no relay
    assert c.client_message(pid, report(4), 31.5) == []
    assert c.tick(31.2) == []
    assert handshake(c, pid, client.DropMe(), 39.0) == [release]


def test_messages_from_unknown_players_and_unruled_messages_are_ignored() -> None:
    c = cyber_city_one()
    pid = joined(c).player_id
    assert c.client_message(9, report(4), 0.0) == []
    assert c.client_message(pid, client.Ping(), 0.0) == []
    assert c.client_message(pid, client.Flags(0), 0.0) == []


def test_leaving_deletes_the_pod_on_the_other_clients_that_hold_it() -> None:
    c = cyber_city_one()
    a, b = joined(c, "A", 0).player_id, joined(c, "B", 0).player_id
    c.player(b).view.hold(a)  # type: ignore[union-attr]  (M5 introduces; here by hand)
    events = c.leave(a, 5.0)
    assert events == [Event(server.Delete(a), ToHolders(a))]
    assert c.player(a) is None
    assert [p.id for p in c.players()] == [b]  # the leaver is no holder any more
    assert c.leave(a, 6.0) == []  # twice is harmless


def churn(c: City, count: int) -> None:
    """``count`` players join team 3 and leave again, one after the other, from t=1."""
    for i in range(count):
        pid = joined(c, "T", 3, now=1.0 + i).player_id
        c.leave(pid, 1.5 + i)


def test_an_exhausted_id_range_is_refused_while_an_old_client_may_hold_the_ids() -> None:
    c = cyber_city_one()
    joined(c, "OLD", 0, now=0.0)  # id 0, started before every release below
    churn(c, 31)  # ids 1..31 used and released
    assert isinstance(c.join("LATE", 2, 50.0), JoinRefused)


def test_released_ids_are_reused_earliest_first_once_every_client_started_after_them() -> None:
    c = cyber_city_one()
    old = joined(c, "OLD", 0, now=0.0).player_id
    churn(c, 30)  # ids 1..30, released at 1.5 .. 30.5
    assert joined(c, "NEW", 2, now=35.0).player_id == 31  # the last fresh id
    assert isinstance(c.join("WAIT", 1, 36.0), JoinRefused)  # OLD (t=0) may hold every released id
    c.leave(old, 40.0)
    assert joined(c, "W", 1, now=50.0).player_id == 1  # the earliest released; NEW started at 35


def test_a_city_resets_when_the_last_player_leaves() -> None:
    c = cyber_city_one()
    pid = joined(c).player_id
    c.leave(pid, 1.0)
    assert joined(c, "NEXT", 0, now=2.0).player_id == 0  # a fresh game, a fresh id space


def test_a_join_whose_b_cannot_be_built_seats_nobody() -> None:
    c = cyber_city_one()
    joined(c, "FIRST")
    before = [p.id for p in c.players()]
    with pytest.raises(UnicodeEncodeError):
        c.join("CAFÉ", 0, 1.0)  # the 'B' carries ASCII only
    assert [p.id for p in c.players()] == before
    assert joined(c, "SECOND", now=2.0).index == 1  # the slot was not taken
