"""The status messages: sent at a session's start and mirrored from the client's reports
(II §11.1, II §11.3, II §12, I §7.8)."""

from __future__ import annotations

from fixtures.cities import seat
from fixtures.delivery import deliveries, to
from fixtures.pods import report
from opencs import policy
from opencs.messages import client, server
from opencs.world.city import City

BAYS = server.SetBays(policy.NEW_PLAYER_BAYS, 0)


def test_the_first_report_is_answered_with_the_bays_then_the_status_to_that_player_alone() -> None:
    c, (a, b) = seat([0, 1], reported=False, max_module_level=4, max_multiplier=20)
    out = deliveries(c, c.client_message(a, report(4), 1.0))
    assert [m for m in to(out, a) if isinstance(m, server.SetBays | server.SetStatus)] == [
        BAYS,
        server.SetStatus(0, 4, 0),
    ]
    assert not [m for m in to(out, b) if isinstance(m, server.SetBays | server.SetStatus)]


def test_the_bays_come_before_the_status() -> None:
    # Without 'D'-'B' the client's multiplier and SCORE never run (I §9.2.3).
    c, (a, _) = seat([0, 1], reported=False)
    kinds = [type(m) for m in to(deliveries(c, c.client_message(a, report(4), 1.0)), a)]
    assert kinds.index(server.SetBays) < kinds.index(server.SetStatus)


def test_the_status_is_sent_once_not_with_every_report() -> None:
    c, (a, _) = seat([0, 1], reported=False)
    c.client_message(a, report(4), 1.0)
    later = deliveries(c, c.client_message(a, report(4), 1.2))
    assert not [m for _, m in later if isinstance(m, server.SetBays | server.SetStatus)]


def test_the_starting_multiplier_is_the_floor_capped_at_the_citys_maximum() -> None:
    for level, cap, floor in ((0, 8, 1), (5, 3, 3), (2, 8, 2)):
        c, (a,) = seat([0], reported=False, max_module_level=level, max_multiplier=cap)
        sent = to(deliveries(c, c.client_message(a, report(4), 1.0)), a)
        assert server.SetStatus(0, floor, 0) in sent


def reported_status(c: City, pid: int, now: float, **kw: int) -> list[server.SetStatus]:
    msg = client.Status(kw.get("score", 100), kw.get("multiplier", 1), kw.get("bay_points", 5))
    out = deliveries(c, c.client_message(pid, msg, now))
    return [m for _, m in out if isinstance(m, server.SetStatus)]


def test_the_clients_status_and_bays_are_mirrored() -> None:
    c, (a, _) = seat([0, 1])
    reported_status(c, a, 5.0, score=4321, multiplier=3, bay_points=88)
    c.client_message(a, client.Bays((2, 2, 1, 1, 1, 0, 0, 0, 0), 1), 6.0)
    mirror = c.player(a).score  # type: ignore[union-attr]
    assert (mirror.score, mirror.multiplier, mirror.bay_points) == (4321, 3, 88)
    assert (mirror.bays, mirror.marker) == ((2, 2, 1, 1, 1, 0, 0, 0, 0), 1)


def test_a_status_report_with_no_death_is_not_answered() -> None:
    c, (a, _) = seat([0, 1], max_module_level=4, max_multiplier=20)
    assert reported_status(c, a, 5.0, multiplier=1) == []
