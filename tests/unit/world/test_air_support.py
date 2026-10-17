"""Air Support: acknowledged at once, flown in by the guppy, a grenade on arrival (I §9.3.6)."""

from __future__ import annotations

from fixtures.cities import seat
from fixtures.delivery import deliveries, to
from fixtures.pods import report
from opencs import policy
from opencs.messages import ServerMessage, WirePos, WireXZ, client, server
from opencs.world.city import City
from opencs.world.logistics import AirSupportJob

TARGET = WireXZ(3, 3, 120, 340)
STAND_Y = 233


def world(teams: list[int] | None = None) -> tuple[City, list[int]]:
    c, ids = seat(teams or [0, 1])
    for pid in ids:
        c.client_message(pid, report(0, cell_x=1, cell_z=1, x_off=50, z_off=50, y=STAND_Y), 1.0)
    return c, ids


def request(c: City, pid: int, priority: int = 0, pos: WireXZ = TARGET, now: float = 10.0):  # type: ignore[no-untyped-def]
    return deliveries(c, c.client_message(pid, client.AirSupport(priority, pos), now))


def fly(
    c: City, pid: int, team: int, legs: int, now: float = 20.0
) -> list[tuple[int, ServerMessage]]:
    out: list[tuple[int, ServerMessage]] = []
    for i in range(legs):
        out += deliveries(c, c.client_message(pid, client.LegArrived(250 + team), now + i))
    return out


def shots(out: list[tuple[int, ServerMessage]], pid: int) -> list[server.Shot]:
    return [m for m in to(out, pid) if isinstance(m, server.Shot)]


def test_the_request_is_acknowledged_to_the_sender_alone_and_a_guppy_is_sent() -> None:
    c, (a, b) = world()
    out = request(c, a)
    assert [m for m in to(out, a) if isinstance(m, server.AirSupportReply)] == [
        server.AirSupportReply(0)
    ]
    assert not [m for m in to(out, b) if isinstance(m, server.AirSupportReply)]
    assert any(isinstance(m, server.MobileCreate) for m in to(out, b))  # the guppy appears
    assert shots(out, a) == []  # nothing is dropped yet


def test_on_arrival_a_kind_9_grenade_by_the_guppy_at_the_point_and_the_senders_altitude() -> None:
    c, (a, b) = world([2, 1])
    request(c, a)
    flight = fly(c, a, 2, 1)
    assert shots(flight, b) == []
    sent = [m for m in to(flight, b) if isinstance(m, server.MobileCreate)]
    assert sent[0].pos == TARGET
    out = fly(c, a, 2, 1, now=40.0)
    expected = server.Shot(252, WirePos(3, 3, 120, STAND_Y, 340), 0, 0, 9)
    assert shots(out, a) == [expected] and shots(out, b) == [expected]
    assert expected.id == policy.GUPPY_BASE_ID + 2


def test_the_altitude_is_the_one_at_request_time() -> None:
    c, (a, _b) = world()
    request(c, a)
    c.client_message(a, report(0, cell_x=1, cell_z=1, y=900), 15.0)
    out = fly(c, a, 0, 2)
    assert shots(out, a)[0].pos.y == STAND_Y


def test_a_client_still_loading_is_not_sent_the_grenade() -> None:
    c, (a, b) = seat([0, 1], reported=False)
    c.client_message(a, report(4, y=STAND_Y), 1.0)
    request(c, a)
    out = fly(c, a, 0, 2)
    assert shots(out, a) and to(out, b) == []


def test_it_is_not_refused_in_an_enemy_no_drop_zone() -> None:
    c, (a, _b) = world()
    enemy_corner = WireXZ(7, 0, 300, 300)  # blue's home tile
    out = request(c, a, pos=enemy_corner)
    assert any(isinstance(m, server.AirSupportReply) for m in to(out, a))
    flight = fly(c, a, 0, 2)
    assert shots(flight, a)[0].pos.cell_x == 7


def test_priority_goes_ahead_of_waiting_jobs_and_is_honored() -> None:
    c, (a, _b) = world()
    for n in range(3):
        c.client_message(a, client.TowerOrder(WirePos(2, 2, 100 + n, 20, 50), 0, 0), 5.0)
    request(c, a, priority=1)
    guppy = c.orders.logistics.guppy(0)
    assert guppy is not None and isinstance(guppy.queue[0], AirSupportJob)
    request(c, a, priority=0, now=11.0)
    last = guppy.queue[-1]
    assert isinstance(last, AirSupportJob) and not last.priority  # to the back
    assert guppy.queue[0].priority  # still first


def test_the_strike_is_logged_as_a_shot_fired() -> None:
    c, (a, _b) = world()
    request(c, a)
    fly(c, a, 0, 2, now=30.0)
    (strike,) = c.orders.strikes
    assert (strike.team, strike.pos.cell_x) == (0, 3)
    assert strike.at == 31.0


def test_a_saturated_queue_acknowledges_but_queues_nothing() -> None:
    c, (a, _b) = world()
    for _ in range(policy.MAX_WAITING_JOBS + 1):
        out = request(c, a)
    assert any(isinstance(m, server.AirSupportReply) for m in to(out, a))
    guppy = c.orders.logistics.guppy(0)
    assert guppy is not None and len(guppy.queue) == policy.MAX_WAITING_JOBS


def test_a_request_before_the_first_report_is_ignored() -> None:
    c, (a,) = seat([0], reported=False)
    assert request(c, a) == []
