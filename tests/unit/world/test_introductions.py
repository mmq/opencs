"""Introductions: 'G' + 'A' once per pair, after the first 'b' of both (I §7.2, I App. A.2)."""

from __future__ import annotations

import pytest

from fixtures.delivery import deliveries, to
from fixtures.lobbies import city
from fixtures.pods import report
from fixtures.spec_vectors import FULL_FRAMES
from opencs import policy
from opencs.messages import ServerMessage, client, server, server_payload
from opencs.wire.frame import build_frame
from opencs.world.city import City, JoinResult
from opencs.world.events import Event, ToAll, ToPlayer
from opencs.world.pods import Pod


def join(c: City, callsign: str, team: int = 0, now: float = 0.0) -> int:
    result = c.join(callsign, team, now)
    assert isinstance(result, JoinResult)
    return result.player_id


def first_b(c: City, pid: int, now: float = 1.0, **kw: int) -> list[tuple[int, ServerMessage]]:
    """The player's report (state 4, the first of a session) and what it delivers, less the relay
    of the 'b' itself (``test_relay`` has that)."""
    out = deliveries(c, c.client_message(pid, report(4, **kw), now))
    return [(p, m) for p, m in out if not isinstance(m, server.PodState)]


def kinds(msgs: list[ServerMessage]) -> list[tuple[str, int]]:
    return [(type(m).__name__, m.id) for m in msgs]  # type: ignore[attr-defined]


def test_nothing_is_introduced_before_a_first_b() -> None:
    c = City(city("A"))
    a, b = join(c, "AAA"), join(c, "BBB")
    assert first_b(c, a) == [(a, server.ReleaseDrop(a))]  # only its own drop; nobody else reported
    assert c.player(a).view.held == {a}  # type: ignore[union-attr]
    assert c.player(b).view.held == {b}  # type: ignore[union-attr]


def test_the_first_b_introduces_each_pair_with_g_then_a_in_both_directions() -> None:
    c = City(city("A"))
    a, b = join(c, "AAA"), join(c, "BBB")
    first_b(c, a)
    out = first_b(c, b, x_off=300)
    assert kinds(to(out, a)) == [("Introduce", b), ("ReleaseDrop", b)]
    assert kinds(to(out, b)) == [
        ("ReleaseDrop", b),  # its own drop
        ("Introduce", a),
        ("ReleaseDrop", a),
    ]
    g_to_a = to(out, a)[0]
    assert isinstance(g_to_a, server.Introduce)
    assert (g_to_a.callsign, g_to_a.index, g_to_a.team) == (
        b"BBB",
        1,
        0,
    )  # the second pilot of team 0
    assert g_to_a.pos.x_off == 300 and g_to_a.pos.y == 240 and g_to_a.orient == 450
    assert (g_to_a.v1, g_to_a.v2, g_to_a.bounty_div10) == (0, 0, 0)
    assert c.player(a).view.holds(b) and c.player(b).view.holds(a)


def test_g_is_followed_by_a_for_the_same_id() -> None:
    c = City(city("A"))
    a, b = join(c, "AAA"), join(c, "BBB")
    first_b(c, a)
    out = first_b(c, b)
    for receiver in (a, b):
        msgs = to(out, receiver)
        pairs = [(m, msgs[i + 1]) for i, m in enumerate(msgs) if isinstance(m, server.Introduce)]
        assert pairs
        for g, after in pairs:
            assert after == server.ReleaseDrop(g.id)


def test_the_g_of_the_worked_session_matches_the_captured_frame() -> None:
    """A.3b: P1's log, the 'G' for P2 (JOINGEN2, id 1, slot 8, cell (0,0), 75/240/75, 450)."""
    c = City(city("A"))
    p1, p2 = join(c, "JOINGENI"), join(c, "JOINGEN2")
    assert (p1, p2) == (0, 1)
    first_b(c, p1, x_off=75, z_off=75)
    out = first_b(c, p2, x_off=75, z_off=75)
    g, a = to(out, p1)
    assert build_frame(server_payload(g, 0x3A)) == FULL_FRAMES["A3b_G"][1]
    assert build_frame(server_payload(a, 0x3B)) == FULL_FRAMES["A3b_A"][1]


def test_a_pod_is_introduced_once_per_client_however_often_it_reports() -> None:
    c = City(city("A"))
    a, b = join(c, "AAA"), join(c, "BBB")
    first_b(c, a)
    first_b(c, b)
    for pid in (a, b):
        again = deliveries(c, c.client_message(pid, report(0), 5.0))
        assert not [m for _, m in again if isinstance(m, server.Introduce)]


def test_a_third_player_is_introduced_pairwise_without_duplicates() -> None:
    c = City(city("A"))
    a, b, d = join(c, "AAA"), join(c, "BBB", 1), join(c, "DDD", 2)
    first_b(c, a)
    first_b(c, b)
    out = first_b(c, d)
    assert kinds([m for m in to(out, d) if isinstance(m, server.Introduce)]) == [
        ("Introduce", a),
        ("Introduce", b),
    ]
    assert kinds([m for m in to(out, a) if isinstance(m, server.Introduce)]) == [("Introduce", d)]
    assert kinds([m for m in to(out, b) if isinstance(m, server.Introduce)]) == [("Introduce", d)]


def test_players_who_have_not_reported_yet_are_introduced_when_they_do() -> None:
    c = City(city("A"))
    a, b, d = join(c, "AAA"), join(c, "BBB", 1), join(c, "DDD", 2)
    first_b(c, a)
    out = first_b(c, d)  # B has not reported: nobody is introduced to it, it to nobody
    assert b not in {pid for pid, _ in out}
    out = first_b(c, b)
    assert sorted(m.id for m in to(out, b) if isinstance(m, server.Introduce)) == [a, d]  # type: ignore[attr-defined]
    assert {pid for pid, m in out if isinstance(m, server.Introduce)} == {a, b, d}


def test_a_player_who_left_is_not_introduced_to_a_later_joiner() -> None:
    c = City(city("A"))
    a, b = join(c, "AAA"), join(c, "BBB", 1)
    first_b(c, a)
    first_b(c, b)
    c.leave(b, 3.0)
    d = join(c, "DDD", 2, now=4.0)
    out = first_b(c, d, now=5.0)
    assert kinds([m for m in to(out, d) if isinstance(m, server.Introduce)]) == [("Introduce", a)]
    assert kinds([m for m in to(out, a) if isinstance(m, server.Introduce)]) == [("Introduce", d)]
    assert d != b  # a fresh id: the client of A never saw this pod


def test_a_dead_pod_is_introduced_where_it_last_stood_not_at_the_fly_over() -> None:
    c = City(city("A"))
    a, b = join(c, "AAA"), join(c, "BBB", 1)
    first_b(c, a)
    c.client_message(a, report(0, x_off=111, y=212), 2.0)  # on the ground at x_off 111
    c.client_message(a, client.Died(), 3.0)
    c.client_message(a, report(4, x_off=500, y=900), 4.0)  # the fly-over camera
    out = first_b(c, b, now=5.0)
    g = to(out, b)[1]
    assert isinstance(g, server.Introduce) and g.id == a
    assert (g.pos.x_off, g.pos.y) == (111, 212)
    assert len(to(out, b)) == 2  # no 'A' yet: the pod is dead for everyone else too
    # Its bare 'A' later releases it on every holder, the newcomer included.
    assert deliveries(c, c.client_message(a, client.DropMe(), 6.0)) == [
        (a, server.ReleaseDrop(a)),
        (b, server.ReleaseDrop(a)),
    ]


def test_the_dead_pods_timeout_release_also_reaches_the_newcomer() -> None:
    c = City(city("A"))
    a, b = join(c, "AAA"), join(c, "BBB", 1)
    first_b(c, a)
    c.client_message(a, report(0), 2.0)
    c.client_message(a, client.Died(), 3.0)
    c.client_message(a, report(4), 4.0)
    first_b(c, b, now=5.0)
    out = deliveries(c, c.tick(3.0 + policy.DROP_REQUEST_TIMEOUT))
    out = [(p, m) for p, m in out if m == server.ReleaseDrop(a)]  # b's own drop ticks too
    assert out == [(a, server.ReleaseDrop(a)), (b, server.ReleaseDrop(a))]


def test_leaving_sends_h_to_every_other_holder_and_to_no_one_else() -> None:
    c = City(city("A"))
    a, b, d = join(c, "AAA"), join(c, "BBB", 1), join(c, "DDD", 2)
    for pid in (a, b, d):
        first_b(c, pid)
    e = join(c, "EEE", 3)  # seated, never reported: holds nobody and nobody holds it
    out = deliveries(c, c.leave(b, 9.0))
    assert out == [(a, server.Delete(b)), (d, server.Delete(b))]  # not b, not e
    assert not c.player(a).view.holds(b) and not c.player(d).view.holds(b)  # type: ignore[union-attr]
    assert c.player(e).view.held == {e}  # type: ignore[union-attr]


def test_a_leaver_is_never_sent_j_which_would_respawn_it() -> None:
    c = City(city("A"))
    a, b = join(c, "AAA"), join(c, "BBB", 1)
    first_b(c, a)
    first_b(c, b)
    messages = [m for _, m in deliveries(c, c.leave(a, 9.0))]
    assert not any(isinstance(m, server.Destroy) for m in messages)


class Tower:
    """A stand-in for the entities of M7-M9: an id and the packets that create it."""

    entity_id = 8192

    def creating_messages(self) -> list[ServerMessage]:
        return [server.Destroy(8192)]  # any message will do


def test_a_spawn_and_a_late_joiners_sync_use_the_same_path_once_per_client() -> None:
    c = City(city("A"))
    a, b = join(c, "AAA"), join(c, "BBB", 1)
    first_b(c, a)
    out = deliveries(c, c.spawn(Tower()))
    assert out == [(a, server.Destroy(8192))]  # b has not reported: not yet
    assert deliveries(c, c.spawn(Tower())) == []  # never a second create
    synced = first_b(c, b)
    assert [m for m in to(synced, b) if isinstance(m, server.Destroy)] == [server.Destroy(8192)]
    assert c.player(b).view.holds(8192)  # type: ignore[union-attr]
    c.despawn(8192)
    d = join(c, "DDD", 2)
    assert not [m for m in to(first_b(c, d), d) if isinstance(m, server.Destroy)]
    assert not c.player(a).view.holds(8192)  # type: ignore[union-attr]


def test_only_the_release_a_is_narrowed_to_the_owner_on_a_first_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Another event a pod might one day return on its first report keeps its own audience.
    other = Event(server.Destroy(7), ToAll())
    real = Pod.on_report

    def on_report(self: Pod, rep: client.PodState, now: float) -> list[Event]:
        return [*real(self, rep, now), other]

    monkeypatch.setattr(Pod, "on_report", on_report)
    c = City(city("A"))
    a = join(c, "AAA")
    events = c.client_message(a, report(4), 1.0)
    assert Event(server.ReleaseDrop(a), ToPlayer(a)) in events
    assert other in events
