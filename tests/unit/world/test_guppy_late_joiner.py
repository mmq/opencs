"""A client that joins mid-flight is given the guppy's leg count, not only its create (I §7.3).

The four steps: (1) the create at home, counted like everyone if the guppy is still on its create
leg; (2) otherwise no other 'K' for that guppy until its copy reports; (3) on each report while
joining, the current destination if the guppy has moved on since the leg last given, else it is in
step; (4) while joining its reports drive nothing."""

from __future__ import annotations

from fixtures.cities import seat
from fixtures.delivery import deliveries, to
from fixtures.pods import report
from opencs.messages import ServerMessage, WirePos, WireXZ, client, server
from opencs.world.city import City

HOME = WireXZ(0, 0, 300, 300)
GUPPY = 250
CREATE = server.MobileCreate(GUPPY, HOME)


def dest(n: int) -> WireXZ:
    return WireXZ(2, 2, 100 + n, 200 + n)


def playing() -> tuple[City, int, int]:
    c, (a, b) = seat([0, 1])
    for pid in (a, b):
        c.client_message(pid, report(0), 1.0)
    return c, a, b


def order(c: City, pid: int, n: int, now: float = 5.0) -> list[tuple[int, ServerMessage]]:
    """A tower order to point ``n``; what it sent, to whom."""
    msg = client.TowerOrder(WirePos(2, 2, 100 + n, 20, 200 + n), 0, 0)
    return deliveries(c, c.client_message(pid, msg, now))


def arrive(c: City, pid: int, now: float = 6.0) -> list[tuple[int, ServerMessage]]:
    return deliveries(c, c.client_message(pid, client.LegArrived(GUPPY), now))


def ks(out: list[tuple[int, ServerMessage]], pid: int) -> list[server.MobileCreate]:
    return [m for m in to(out, pid) if isinstance(m, server.MobileCreate)]


def towers(out: list[tuple[int, ServerMessage]], pid: int) -> list[server.SpawnTower]:
    return [m for m in to(out, pid) if isinstance(m, server.SpawnTower)]


def join(
    c: City, name: str = "LATE", team: int = 2, now: float = 20.0
) -> tuple[int, list[ServerMessage]]:
    """A third client joins and sends its first report; its id and everything it was sent."""
    r = c.join(name, team, now)
    assert hasattr(r, "player_id")
    pid = r.player_id  # type: ignore[union-attr]
    out = deliveries(c, c.client_message(pid, report(4), now + 0.5))
    return pid, to(out, pid)


# --- step 1: the create, and counting like everyone while the create leg is open ---


def test_a_joiner_during_the_create_leg_counts_like_everyone() -> None:
    c, a, _b = playing()
    order(c, a, 1)
    j, got = join(c)
    assert [m for m in got if isinstance(m, server.MobileCreate)] == [CREATE]
    arrive(c, a)  # A's create report: the guppy is sent to the point
    # J's copy reports the create too: a duplicate leg 0, nothing happens and nothing is sent.
    assert arrive(c, j) == []
    out = arrive(c, j)  # J's leg 1: first to report it, so it acts
    assert towers(out, a) and towers(out, j)
    # It was sent the dispatch with everybody else, as it counts from the start.


def test_a_joiner_during_the_create_leg_is_sent_the_dispatch() -> None:
    c, a, _b = playing()
    order(c, a, 1)
    j, _ = join(c)
    out = arrive(c, a)
    assert ks(out, j) == [server.MobileCreate(GUPPY, dest(1))]


# --- step 2: no other 'K' until its copy reports ---


def test_a_joiner_while_the_guppy_flies_is_sent_the_create_and_nothing_else_until_it_reports() -> (
    None
):
    c, a, _b = playing()
    order(c, a, 1)
    arrive(c, a)  # outbound to point 1
    j, got = join(c)
    assert ks([(j, m) for m in got], j) == [CREATE]
    # More traffic for the guppy while J is joining: order 2 queued, delivery, next dispatch.
    order(c, a, 2)
    out = arrive(c, a)  # delivered; job 2 dispatched
    assert ks(out, a) == [server.MobileCreate(GUPPY, dest(2))]
    assert ks(out, j) == []  # J's copy still sits at home
    assert towers(out, j)  # but it is told about the tower, like everyone


# --- step 3: on each report, the current destination if the guppy has moved on ---


def test_a_joiners_first_report_during_a_flight_is_answered_with_the_current_destination() -> None:
    c, a, _b = playing()
    order(c, a, 1)
    arrive(c, a)  # outbound to point 1
    j, _ = join(c)
    out = arrive(c, j)  # its create report
    assert out == [(j, server.MobileCreate(GUPPY, dest(1)))]  # to J alone


def test_after_the_catch_up_the_next_report_is_the_current_legs_arrival() -> None:
    c, a, b = playing()
    order(c, a, 1)
    arrive(c, a)
    j, _ = join(c)
    arrive(c, j)  # caught up to leg 1
    out = arrive(c, j)  # J's copy arrived at point 1; nobody else has reported leg 1
    assert towers(out, a) and towers(out, j) and towers(out, b)
    assert ks(out, j) == [server.MobileCreate(GUPPY, HOME)]  # and J is now counted in


def test_the_catch_up_is_repeated_if_the_guppy_moved_on_while_the_joiner_flew() -> None:
    c, a, _b = playing()
    order(c, a, 1)
    arrive(c, a)  # outbound
    j, _ = join(c)
    arrive(c, j)  # caught up to the outbound leg
    arrive(c, a)  # A reports the arrival: delivered, guppy RETURNING (a newer leg)
    out = arrive(c, j)  # J's arrival report is for the old leg: send it home too
    assert out == [(j, server.MobileCreate(GUPPY, HOME))]
    assert arrive(c, j) == []  # J's copy is home: counted in; the guppy is home too
    assert arrive(c, a) == []  # A's report of the same leg is a duplicate


def test_a_joiner_while_the_guppy_returns_is_sent_home_on_its_first_report() -> None:
    c, a, _b = playing()
    order(c, a, 1)
    arrive(c, a)
    arrive(c, a)  # delivered: RETURNING
    j, got = join(c)
    assert [m for m in got if isinstance(m, server.MobileCreate)] == [CREATE]
    out = arrive(c, j)
    assert out == [(j, server.MobileCreate(GUPPY, HOME))]
    assert arrive(c, j) == []  # its return-leg report: counted in, nothing to do for the guppy


def test_a_joiner_at_home_is_counted_in_by_its_create_report_and_flies_the_next_order() -> None:
    c, a, _b = playing()
    order(c, a, 1)
    arrive(c, a)
    arrive(c, a)
    arrive(c, a)  # home, idle
    j, got = join(c)
    assert [m for m in got if isinstance(m, server.MobileCreate)] == [CREATE]
    assert arrive(c, j) == []  # in step: counted in, silent
    # The guppy was idle at home, so the order dispatches at once, to J as to everyone.
    assert ks(order(c, a, 2, 30.0), j) == [server.MobileCreate(GUPPY, dest(2))]
    # J's next report is the next leg's arrival, not a duplicate of an older one.
    out = arrive(c, j)
    assert towers(out, a)


def test_a_joiner_caught_up_to_a_drop_point_is_sent_home_once_the_guppy_is_home_again() -> None:
    # Step 3's "you already sent a catch-up" branch: the guppy is home now (not in flight), but J's
    # copy is still parked at the point it was caught up to.
    c, a, _b = playing()
    order(c, a, 1)
    arrive(c, a)  # create reported: outbound
    j, _ = join(c)
    assert arrive(c, j) == [(j, server.MobileCreate(GUPPY, dest(1)))]  # caught up to the point
    arrive(c, a)  # arrival: delivered, returning
    arrive(c, a)  # home leg: the guppy is HOME, nothing in flight
    out = arrive(c, j)  # J's copy reports its arrival at the point
    assert out == [(j, server.MobileCreate(GUPPY, HOME))]  # to J alone
    assert arrive(c, j) == []  # J's copy is home: counted in, silent
    assert c._logistics.ignored_reports == 0
    # And it is counted in at the right leg: the next order is flown and reported by everyone.
    assert ks(order(c, a, 2, 40.0), j) == [server.MobileCreate(GUPPY, dest(2))]
    assert towers(arrive(c, j), j)


# --- step 4: while joining its reports drive nothing ---


def test_a_joining_clients_report_never_delivers_or_starts_anything() -> None:
    c, a, _b = playing()
    order(c, a, 1)
    arrive(c, a)  # outbound
    j, _ = join(c)
    out = arrive(c, j)  # create report while joining: only the catch-up
    assert towers(out, a) == [] and towers(out, j) == []
    assert c._logistics.ignored_reports == 0


# --- the originals leave ---


def test_after_the_originals_leave_the_joiner_alone_completes_legs() -> None:
    c, a, b = playing()
    order(c, a, 1)
    arrive(c, a)  # outbound
    j, _ = join(c, team=0)
    arrive(c, j)  # caught up
    c.leave(a, 25.0)
    c.leave(b, 25.0)
    out = arrive(c, j, 26.0)  # arrival at point 1
    assert towers(out, j)
    assert ks(out, j) == [server.MobileCreate(GUPPY, HOME)]
    order(c, j, 2, 27.0)  # queued: RETURNING
    out = arrive(c, j, 28.0)  # home: job 2 goes out
    assert ks(out, j) == [server.MobileCreate(GUPPY, dest(2))]
    assert towers(arrive(c, j, 29.0), j)
    assert c._logistics.ignored_reports == 0


# --- a client's count is bounded by the legs it was sent (I §9.4.1) ---


def test_a_report_beyond_the_legs_sent_is_ignored_and_does_not_advance_the_count() -> None:
    c, a, _b = playing()
    order(c, a, 1)  # the create is sent: one report is owed
    count = c.player(a).view.legs[GUPPY]  # type: ignore[union-attr]
    assert count.owed == 1
    arrive(c, a)  # the create leg is reported; the dispatch to point 1 is sent
    assert (count.owed, count.reports) == (1, 1)
    assert towers(arrive(c, a), a)  # leg 1, owed: delivers the tower
    assert (count.owed, count.reports) == (1, 2)  # and the way home was sent
    arrive(c, a)  # home
    assert (count.owed, count.reports) == (0, 3)
    assert c._logistics.ignored_reports == 0
    assert arrive(c, a) == []  # nothing is owed: a modified client reporting early
    assert (count.owed, count.reports) == (0, 3)  # the count did not run ahead
    assert c._logistics.ignored_reports == 1


def test_early_reports_cannot_pre_report_a_leg_that_has_not_been_sent() -> None:
    # The cheat the bound limits: report the legs of a long queue before they are flown.
    c, a, b = playing()
    for n in range(1, 4):
        order(c, a, n)
    arrive(c, a)
    for _ in range(10):
        arrive(c, a)
    # a's reports delivered what was sent to it (one tower, the way home, then the next job...)
    # but never more legs than it was told about.
    count = c.player(a).view.legs[GUPPY]  # type: ignore[union-attr]
    guppy = c._logistics.guppy(0)
    assert guppy is not None
    assert count.reports <= guppy.legs
    assert c._logistics.ignored_reports >= 1
    # b has been sent the same legs and is unaffected: its own reports are all honored.
    sent_to_b = c.player(b).view.legs[GUPPY].owed  # type: ignore[union-attr]
    assert sent_to_b == guppy.legs


def test_a_late_joiners_real_peak_is_never_ignored() -> None:
    # A joiner owes its create report, then one per catch-up; honest reports never exceed that.
    c, a, _b = playing()
    order(c, a, 1)
    arrive(c, a)  # outbound
    j, _ = join(c)
    count = c.player(j).view.legs[GUPPY]  # type: ignore[union-attr]
    assert count.owed == 1  # only the create so far
    arrive(c, j)  # create report -> catch-up to the point
    assert count.owed == 1
    arrive(c, a)  # delivered; the guppy returns
    arrive(c, j)  # arrival at the old leg -> catch-up home
    assert count.owed == 1
    arrive(c, j)  # home: counted in
    assert count.owed == 0
    assert c._logistics.ignored_reports == 0
    assert arrive(c, j) == []  # one more than it was sent
    assert c._logistics.ignored_reports == 1
