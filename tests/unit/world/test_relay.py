"""The 'b' relay: state 1 or 0, motion passed through, signed offsets kept, owner excluded
(I §6.1, I §8.24, I App. A.2)."""

from __future__ import annotations

import pytest

from fixtures.delivery import deliveries, to
from fixtures.lobbies import city
from fixtures.pods import report
from fixtures.spec_vectors import FULL_FRAMES
from opencs.messages import client, decode_server, server, server_payload
from opencs.wire.frame import build_frame
from opencs.world import relay
from opencs.world.city import City, JoinResult


@pytest.mark.parametrize(("sent", "relayed"), [(4, 0), (2, 0), (0, 0), (1, 1)])
def test_the_relayed_state_is_1_if_the_reporter_is_falling_otherwise_0(
    sent: int, relayed: int
) -> None:
    assert relay.pod_state(7, report(sent)).state == relayed


def test_motion_fields_are_passed_through_with_the_state_they_belong_to() -> None:
    falling = relay.pod_state(7, report(1, v1=-12, v2=345, fwd=-9, fwd_target=17, weight=33))
    assert (falling.v1, falling.v2, falling.fwd, falling.fwd_target) == (-12, 345, -9, 17)
    assert falling.weight == 33 and falling.id == 7 and falling.state == 1
    walking = relay.pod_state(7, report(0, v1=21, v2=-6))  # turn rates: same values, state 0
    assert (walking.v1, walking.v2, walking.state) == (21, -6, 0)


def test_signed_offsets_are_kept_never_clamped() -> None:
    off_map = report(0, x_off=-40, z_off=-123, y=-5, orient=3599)
    msg = relay.pod_state(7, off_map)
    assert (msg.pos.cell_x, msg.pos.cell_z, msg.pos.x_off, msg.pos.z_off) == (0, 0, -40, -123)
    assert msg.pos.y == -5 and msg.orient == 3599
    on_wire, _ = decode_server(server_payload(msg, 0))  # the offsets survive the encoding
    assert on_wire == msg


def _two() -> tuple[City, int, int]:
    c = City(city("A"))
    ids = []
    for callsign in ("AAA", "BBB"):
        r = c.join(callsign, 0, 0.0)
        assert isinstance(r, JoinResult)
        ids.append(r.player_id)
    return c, ids[0], ids[1]


def relayed(out: list[tuple[int, object]]) -> list[tuple[int, server.PodState]]:
    return [(pid, m) for pid, m in out if isinstance(m, server.PodState)]


def test_a_report_is_relayed_to_the_other_holder_never_to_its_owner() -> None:
    c, a, b = _two()
    c.client_message(a, report(4), 1.0)
    c.client_message(b, report(4), 1.0)
    out = deliveries(c, c.client_message(a, report(0, x_off=99), 2.0))
    assert [pid for pid, _ in relayed(out)] == [b]
    (_, msg) = relayed(out)[0]
    assert msg.id == a and msg.pos.x_off == 99


def test_nothing_is_relayed_to_a_client_that_has_not_been_introduced() -> None:
    c, a, b = _two()
    out = deliveries(c, c.client_message(a, report(4), 1.0))
    assert relayed(out) == []  # b is seated but a is nobody's pod yet
    out = deliveries(c, c.client_message(a, report(0), 2.0))
    assert relayed(out) == []  # b has not reported, so was not introduced to a
    out = deliveries(c, c.client_message(b, report(4), 3.0))
    assert [pid for pid, _ in relayed(out)] == [a]  # the relay follows the introductions
    assert [type(m).__name__ for m in to(out, a)] == ["Introduce", "ReleaseDrop", "PodState"]


def test_the_relay_of_the_first_b_comes_after_the_g_and_a_and_matches_the_capture() -> None:
    """A.3b P1's log: the introduction of P2, then 'b' id 1, state 0, at 75/240/75."""
    c, a, b = _two()
    c.client_message(a, report(4, x_off=75, z_off=75), 1.0)
    out = deliveries(c, c.client_message(b, report(4, x_off=75, z_off=75), 2.0))
    *_, last = to(out, a)
    assert build_frame(server_payload(last, 0)) == FULL_FRAMES["A3b_b"][1]


def test_a_dead_pods_fly_over_reports_are_not_relayed() -> None:
    c, a, b = _two()
    c.client_message(a, report(4), 1.0)
    c.client_message(b, report(4), 1.0)
    c.client_message(a, client.Died(), 2.0)
    out = deliveries(c, c.client_message(a, report(4, x_off=900), 3.0))
    assert relayed(out) == []
    c.client_message(a, client.DropMe(), 4.0)  # the pod drops again and is placed again
    out = deliveries(c, c.client_message(a, report(4, x_off=900), 4.1))
    assert [m.state for _, m in relayed(out)] == [0]
