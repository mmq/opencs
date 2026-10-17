"""A client that joins while a Muncher is out is given a copy of its own (I §7.3, I App. B): its
create and an immediate dispatch, never a live ``'K'`` or ``'E'``-``'C'`` before them, and after
that copy's first report the next leg, or the latch if it is eating."""

from __future__ import annotations

from typing import NamedTuple

from fixtures.delivery import to
from fixtures.munchers import (
    FIRST,
    arrive,
    call,
    destroys,
    hit,
    join_late,
    ks,
    latches,
    tick,
    tower,
    world,
    xz,
)
from opencs.messages import server
from opencs.world.city import City

T1 = (1500, 1800)
T2 = (1500, 2300)
HOME = (1500, 1500)


class Trip(NamedTuple):
    c: City
    a: int
    b: int
    one: int
    two: int


def trip() -> Trip:
    """Two clients, two towers 500 ft apart, a Muncher called at HOME and not yet reported."""
    c, (a, b) = world()
    one = tower(c, *T1)
    two = tower(c, *T2)
    call(c, a)
    return Trip(c, a, b, one, two)


def creates(msgs: list[server.MobileCreate | object]) -> list[server.MobileCreate]:
    return [m for m in msgs if isinstance(m, server.MobileCreate)]


def test_a_joiner_even_in_the_first_leg_is_joining_because_its_copy_started_late() -> None:
    t = trip()
    c, a, one = t.c, t.a, t.one
    late, got = join_late(c, 12.0)
    assert got and creates(got) == [
        server.MobileCreate(FIRST, xz(*HOME)),
        server.MobileCreate(FIRST, xz(*T1)),
    ]
    # The others' arrival latches them; the late copy is still flying and is not told.
    assert latches(arrive(c, a, FIRST, 16.0), late) == []
    # Its own report is then the arrival: it is latched, once.
    assert latches(arrive(c, late, FIRST, 20.0), late) == [server.SendMuncher(FIRST, one)]


def test_a_client_present_when_it_was_called_flies_it_from_the_start_like_everyone() -> None:
    t = trip()
    c, a, b, one = t.c, t.a, t.b, t.one
    assert not c.player(b).view.munchers[FIRST].joining  # type: ignore[union-attr]
    assert latches(arrive(c, a, FIRST, 16.0), b) == [server.SendMuncher(FIRST, one)]


def test_a_joiner_is_created_where_the_current_leg_began_and_dispatched_at_once() -> None:
    t = trip()
    c, a = t.c, t.a
    arrive(c, a, FIRST, 16.0)
    tick(c, 31.0)  # tower one eaten; leg 2 flies from tower one to tower two
    _, got = join_late(c, 33.0)
    assert creates(got) == [
        server.MobileCreate(FIRST, xz(*T1)),  # where leg 2 began
        server.MobileCreate(FIRST, xz(*T2)),  # and where it is going
    ]


def test_a_joining_client_is_sent_no_live_traffic_before_its_copy_has_reported() -> None:
    t = trip()
    c, a, b, two = t.c, t.a, t.b, t.two
    arrive(c, a, FIRST, 16.0)
    tick(c, 31.0)
    late, _ = join_late(c, 33.0)
    out = arrive(c, a, FIRST, 40.0)  # leg 2 arrives at the others: latched on tower two
    assert latches(out, a) and latches(out, b)
    assert to(out, late) == []  # no latch for a copy that never saw leg 2 arrive
    out = tick(c, 55.0)  # tower two eaten, the Muncher leaves
    assert ks(out, a) and ks(out, b)
    assert to(out, late) == [server.Destroy(two)]  # the tower's 'J' only; no stray 'K'
    assert not [m for m in to(out, late) if isinstance(m, server.MobileCreate)]


def test_its_first_report_while_the_muncher_has_moved_on_is_answered_with_the_current_leg() -> None:
    t = trip()
    c, a = t.c, t.a
    arrive(c, a, FIRST, 16.0)
    tick(c, 31.0)
    late, _ = join_late(c, 33.0)
    arrive(c, a, FIRST, 40.0)
    tick(c, 55.0)  # leg 3: to the edge
    out = arrive(c, late, FIRST, 56.0)  # the copy finished leg 2
    edge = c.munchers.alive[FIRST].destination
    assert ks(out, late) == [edge]


def test_after_that_catch_up_its_next_report_is_the_arrival_and_it_is_in_step() -> None:
    t = trip()
    c, a = t.c, t.a
    arrive(c, a, FIRST, 16.0)
    tick(c, 31.0)
    late, _ = join_late(c, 33.0)
    arrive(c, a, FIRST, 40.0)
    tick(c, 55.0)
    arrive(c, late, FIRST, 56.0)  # caught up with leg 3
    out = arrive(c, late, FIRST, 70.0)  # it reached the edge first: the first report of leg 3
    assert destroys(out, late) == [FIRST] and destroys(out, a) == [FIRST]


def test_a_joiner_during_the_meal_is_created_on_the_tower_and_latched_after_its_report() -> None:
    t = trip()
    c, a, one = t.c, t.a, t.one
    arrive(c, a, FIRST, 16.0)  # latched on tower one, eating
    late, got = join_late(c, 20.0)
    assert creates(got) == [
        server.MobileCreate(FIRST, xz(*T1)),  # on the tower itself
        server.MobileCreate(FIRST, xz(*T1)),
    ]
    assert not [m for m in got if isinstance(m, server.SendMuncher)]  # not before its report
    out = arrive(c, late, FIRST, 21.0)
    assert latches(out, late) == [server.SendMuncher(FIRST, one)]
    assert creates(to(out, late)) == []


def test_a_meal_that_ends_before_the_joiners_report_does_not_send_it_the_next_leg() -> None:
    t = trip()
    c, a, one = t.c, t.a, t.one
    arrive(c, a, FIRST, 16.0)
    late, _ = join_late(c, 29.0)
    out = tick(c, 31.0)  # eaten; leg 2 to tower two goes to a and b only
    assert ks(out, a) == [xz(*T2)] and ks(out, late) == []
    assert destroys(out, late) == [one]
    out = arrive(c, late, FIRST, 32.0)  # its copy reached tower one: told where to go now
    assert ks(out, late) == [xz(*T2)] and latches(out, late) == []


def test_the_joiner_alone_carries_the_muncher_through_the_rest_of_its_trip() -> None:
    t = trip()
    c, a, b, one, two = t.c, t.a, t.b, t.one, t.two
    arrive(c, a, FIRST, 16.0)
    late, _ = join_late(c, 20.0)
    c.leave(a, 21.0)
    c.leave(b, 22.0)
    assert latches(arrive(c, late, FIRST, 23.0), late)  # the copy's latch after its report
    out = tick(c, 31.0)
    assert destroys(out, late) == [one] and ks(out, late) == [xz(*T2)]
    assert latches(arrive(c, late, FIRST, 40.0), late) == [server.SendMuncher(FIRST, two)]
    out = tick(c, 55.0)
    assert destroys(out, late) == [two] and ks(out, late)
    out = arrive(c, late, FIRST, 70.0)
    assert destroys(out, late) == [FIRST]
    assert FIRST not in c.munchers.alive


def test_a_joining_client_is_told_of_the_removal_so_it_never_keeps_a_stray_copy() -> None:
    t = trip()
    c, a = t.c, t.a
    arrive(c, a, FIRST, 16.0)
    late, _ = join_late(c, 20.0)
    for _ in range(6):
        out = hit(c, a, FIRST, now=22.0)
    assert destroys(out, late) == [FIRST]


def test_a_joining_client_cannot_report_more_legs_than_it_was_sent() -> None:
    t = trip()
    c, a = t.c, t.a
    arrive(c, a, FIRST, 16.0)
    late, _ = join_late(c, 20.0)
    arrive(c, late, FIRST, 21.0)  # in step: latched
    assert arrive(c, late, FIRST, 21.1) == []
    assert c.munchers.ignored_reports == 1


def test_a_joiner_in_the_leaving_leg_is_created_where_it_began_and_sent_to_the_edge() -> None:
    t = trip()
    c, a = t.c, t.a
    arrive(c, a, FIRST, 16.0)
    tick(c, 31.0)
    arrive(c, a, FIRST, 40.0)
    tick(c, 55.0)  # leaving, from tower two
    _, got = join_late(c, 57.0)
    edge = c.munchers.alive[FIRST].destination
    assert creates(got) == [server.MobileCreate(FIRST, xz(*T2)), server.MobileCreate(FIRST, edge)]


def test_nothing_reaches_a_client_before_the_create_it_belongs_to_whatever_the_join_time() -> None:
    """The property of I §7.3 over a whole trip: for every join moment, the first Muncher message a
    joiner is ever sent is a create (a ``'K'`` followed at once by its dispatch)."""
    # (report an arrival | tick to this time): the whole trip, as the others drive it.
    script: list[tuple[str, float]] = [
        ("arrive", 16.0),
        ("tick", 31.0),
        ("arrive", 40.0),
        ("tick", 55.0),
        ("arrive", 70.0),
    ]
    for join_at in range(len(script) + 1):
        t = trip()
        c, a = t.c, t.a
        late: int | None = None
        seen: list[object] = []
        for i, (what, when) in enumerate([*script, ("tick", 99.0)]):
            if i == join_at:
                late, got = join_late(c, 100.0 + i)
                seen += got
            out = arrive(c, a, FIRST, when) if what == "arrive" else tick(c, when)
            if late is not None:
                seen += to(out, late)
        muncher = [m for m in seen if isinstance(m, (server.MobileCreate, server.SendMuncher))]
        if muncher:
            assert [type(m) for m in muncher[:2]] == [server.MobileCreate, server.MobileCreate]
