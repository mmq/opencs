"""The spawn grace mirror: armed on spawns, ended by leaving the home box or by 120 s (I §9.1.5)."""

from __future__ import annotations

import logging

import pytest

from fixtures.cities import seat
from fixtures.pods import report
from opencs import policy
from opencs.messages import WirePos, client
from opencs.world.grace import Grace, home_box


def at(world_x: int, world_z: int) -> WirePos:
    return WirePos.from_world(world_x, 240, world_z)


@pytest.mark.parametrize(
    ("team", "box"),
    [
        (0, (-600, -600, 1200, 1200)),  # the spec: team 0 keeps grace up to x, z = 1200
        (1, (3600, -600, 5400, 1200)),
        (2, (-600, 3600, 1200, 5400)),
        (3, (3600, 3600, 5400, 5400)),
    ],
)
def test_the_home_box_is_the_3x3_tiles_around_the_teams_corner(
    team: int, box: tuple[int, int, int, int]
) -> None:
    assert home_box(team) == box


def test_grace_is_not_active_until_armed() -> None:
    g = Grace(0, 0)
    g.observe(at(3000, 3000), 1.0)
    assert not g.active


def test_the_edge_of_the_box_is_inside_and_one_foot_beyond_ends_grace() -> None:
    g = Grace(0, 0)
    g.arm(0.0)
    g.observe(at(1200, 1200), 1.0)
    assert g.active  # strictly outside ends it, so 1200 does not
    g.observe(at(1201, 500), 2.0)
    assert not g.active


@pytest.mark.parametrize(("x", "z"), [(1201, 0), (0, 1201), (-601, 0), (0, -601)])
def test_leaving_on_any_side_ends_grace(x: int, z: int) -> None:
    g = Grace(0, 0)
    g.arm(0.0)
    g.observe(at(x, z), 1.0)
    assert not g.active


def test_grace_times_out_after_two_minutes_inside_the_box() -> None:
    g = Grace(0, 0)
    g.arm(10.0)
    g.tick(10.0 + policy.GRACE_TIMEOUT - 0.01)
    assert g.active
    g.tick(10.0 + policy.GRACE_TIMEOUT)
    assert not g.active


def test_a_report_after_the_timeout_ends_grace_too() -> None:
    g = Grace(0, 0)
    g.arm(0.0)
    g.observe(at(100, 100), policy.GRACE_TIMEOUT)
    assert not g.active


def test_the_transition_is_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    g = Grace(4, 1)
    g.arm(0.0)
    with caplog.at_level(logging.INFO, logger="opencs.world.grace"):
        g.observe(at(100, 100), 1.0)  # team 1's box is at x 3600+: outside
        g.observe(at(100, 100), 2.0)
    assert [r.getMessage() for r in caplog.records] == [
        "pod 4: spawn grace ended after 1.0 s (left the home box)"
    ]


def test_a_city_arms_grace_at_the_first_report_and_ends_it_when_the_pod_walks_out() -> None:
    c, (a, _) = seat([0, 1], reported=False)
    pa = c.player(a)
    assert pa is not None and not pa.grace.active
    c.client_message(a, report(4, cell_x=0, cell_z=0, x_off=145, z_off=5), 1.0)
    assert pa.grace.active
    c.client_message(a, report(0, cell_x=3, cell_z=3), 5.0)
    assert not pa.grace.active


def test_a_death_re_arms_grace_and_the_fly_over_camera_does_not_end_it() -> None:
    c, (a, _) = seat([0, 1])
    pa = c.player(a)
    assert pa is not None
    c.client_message(a, report(0, cell_x=3, cell_z=3), 5.0)
    assert not pa.grace.active
    c.client_message(a, client.Died(), 20.0)
    assert pa.grace.active
    c.client_message(a, report(4, cell_x=5, cell_z=5), 22.0)  # the camera, far from the corner
    assert pa.grace.active
    c.client_message(a, client.DropMe(), 29.0)
    c.client_message(a, report(4, cell_x=0, cell_z=0), 29.5)  # dropped at the corner
    assert pa.grace.active
    c.client_message(a, report(0, cell_x=2, cell_z=2), 35.0)
    assert not pa.grace.active


def test_the_citys_tick_times_grace_out() -> None:
    c, (a, _) = seat([0, 1])
    pa = c.player(a)
    assert pa is not None and pa.grace.active
    c.tick(0.5 + policy.GRACE_TIMEOUT)
    assert not pa.grace.active
