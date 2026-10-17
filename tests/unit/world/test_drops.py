"""Dropped modules: sync, rest, pickup, eject and used-up (I §9.4, I §9.3.5, I §7.3, I §9.3.3)."""

from __future__ import annotations

import logging

import pytest

from fixtures.cities import seat
from fixtures.delivery import deliveries, to
from fixtures.pods import report
from opencs import policy
from opencs.messages import ServerMessage, WirePos, client, server
from opencs.world.city import City

REPAIR, ROCKET_PACK = 6, 5
SPOT = WirePos(0, 0, 100, 30, 100)
FIRST = policy.STRUCTURE_IDS[0]


def setup(teams: list[int]) -> tuple[City, list[int]]:
    c, ids = seat(teams)
    for pid in ids:
        c.client_message(pid, report(0, x_off=100, z_off=100), 1.0)
    return c, ids


def drop(c: City, module: int = REPAIR, pos: WirePos = SPOT) -> int:
    out = deliveries(c, c.drops.create(module, pos, 2.0))
    return next(m.id for _, m in out if isinstance(m, server.SpawnModule))


def sends(out: list[tuple[int, ServerMessage]], pid: int, kind: type) -> list:  # type: ignore[type-arg]
    return [m for m in to(out, pid) if isinstance(m, kind)]


def pick(c: City, pid: int, drop_id: int, now: float = 3.0) -> list[tuple[int, ServerMessage]]:
    return deliveries(c, c.client_message(pid, client.PickupRequest(drop_id), now))


# --- the drop in the world ---


def test_a_drop_goes_to_every_client_that_has_reported_with_an_id_of_8192_or_more() -> None:
    c, (a, b) = setup([0, 1])
    out = deliveries(c, c.drops.create(REPAIR, SPOT, 2.0, (1, 1, 0)))
    expected = server.SpawnModule(FIRST, REPAIR, SPOT, 1, 1, 0)
    assert out == [(a, expected), (b, expected)]
    assert FIRST >= 8192


def test_a_client_still_loading_gets_the_drop_when_it_reports_and_only_once() -> None:
    c, (a, b) = seat([0, 1], reported=False)
    c.client_message(a, report(4), 1.0)
    out = deliveries(c, c.drops.create(REPAIR, SPOT, 2.0))
    assert to(out, b) == [] and len(to(out, a)) == 1
    later = deliveries(c, c.client_message(b, report(4), 3.0))
    assert sends(later, b, server.SpawnModule) == [server.SpawnModule(FIRST, REPAIR, SPOT)]
    again = deliveries(c, c.client_message(b, report(0), 4.0))
    assert sends(again, b, server.SpawnModule) == []


def test_rest_updates_the_position_a_later_joiner_is_given_and_zeroes_its_velocity() -> None:
    c, (a, b) = seat([0, 1], reported=False)
    c.client_message(a, report(4), 1.0)
    did = deliveries(c, c.drops.create(REPAIR, SPOT, 2.0, (1, 1, 0)))[0][1].id  # type: ignore[attr-defined]
    rest = WirePos(0, 0, 118, 0, 100)
    c.client_message(a, client.ModuleRested(did, rest), 3.0)
    later = deliveries(c, c.client_message(b, report(4), 4.0))
    assert sends(later, b, server.SpawnModule) == [server.SpawnModule(did, REPAIR, rest, 0, 0, 0)]


def test_a_rest_report_for_an_unknown_or_unheld_drop_is_ignored() -> None:
    c, (a, b) = seat([0, 1], reported=False)
    c.client_message(a, report(4), 1.0)
    did = deliveries(c, c.drops.create(REPAIR, SPOT, 2.0))[0][1].id  # type: ignore[attr-defined]
    c.client_message(a, client.ModuleRested(99999, WirePos(1, 1, 1, 1, 1)), 3.0)
    c.client_message(b, client.ModuleRested(did, WirePos(1, 1, 1, 1, 1)), 3.0)  # b holds nothing
    assert c.drops.drops[did].pos == SPOT


# --- pickup ---


def test_pickup_grants_the_picker_alone_and_destroys_the_drop_for_everyone_else() -> None:
    c, (a, b, d) = setup([0, 1, 2])
    did = drop(c)
    out = pick(c, b, did)
    assert to(out, b) == [server.GrantPickup(did)]
    assert to(out, a) == [server.DestroyObject(did)]
    assert to(out, d) == [server.DestroyObject(did)]


def test_the_grant_is_not_a_destroy_and_the_destroy_never_reaches_the_picker() -> None:
    c, (a, b) = setup([0, 1])
    did = drop(c)
    out = pick(c, a, did)
    assert not sends(out, a, server.DestroyObject)
    assert not sends(out, b, server.GrantPickup)


def test_a_picked_up_drop_is_forgotten_and_a_later_joiner_is_not_given_it() -> None:
    c, (a, b) = seat([0, 1], reported=False)
    c.client_message(a, report(4), 1.0)
    did = deliveries(c, c.drops.create(REPAIR, SPOT, 2.0))[0][1].id  # type: ignore[attr-defined]
    pick(c, a, did)
    assert did not in c.drops.drops and not c.player(a).view.holds(did)  # type: ignore[union-attr]
    later = deliveries(c, c.client_message(b, report(4), 4.0))
    assert sends(later, b, server.SpawnModule) == []


def test_a_second_pickup_of_the_same_drop_is_answered_with_nothing() -> None:
    c, (a, b) = setup([0, 1])
    did = drop(c)
    pick(c, a, did)
    assert pick(c, b, did, now=3.1) == []
    assert c.player(b).loadout.carried == []  # type: ignore[union-attr]
    assert c.player(a).loadout.carried == [REPAIR]  # type: ignore[union-attr]


def test_the_released_id_is_not_reused_while_a_client_may_hold_it() -> None:
    c, (a, _b) = setup([0, 1])
    first = drop(c)
    pick(c, a, first)
    assert drop(c) == first + 1  # fresh ids go out in order (I §6.2, II §8)


def test_a_pickup_names_the_ledger_with_the_drops_module_as_pending() -> None:
    c, (a, _b) = setup([0, 1])
    pick(c, a, drop(c, ROCKET_PACK))
    player = c.player(a)
    assert player is not None and player.loadout.pending == [ROCKET_PACK]
    c.client_message(a, report(0, weight=4), 4.0)
    assert player.loadout.modules == [ROCKET_PACK]


def test_a_pickup_of_an_unknown_id_or_a_tower_does_nothing() -> None:
    c, (a, _b) = setup([0, 1])
    assert pick(c, a, 12345) == []
    c.orders.place_tower(0, SPOT, False, 2.0)
    assert pick(c, a, FIRST) == []
    assert c.orders.towers  # the tower stands


def test_a_pickup_from_far_away_is_logged_not_refused(caplog: pytest.LogCaptureFixture) -> None:
    c, (a, _b) = setup([0, 1])
    far = WirePos(3, 3, 100, 30, 100)
    did = drop(c, pos=far)
    with caplog.at_level(logging.INFO, logger="opencs.world"):
        out = pick(c, a, did)
    assert to(out, a) == [server.GrantPickup(did)]
    assert any("beyond 50 ft" in r.getMessage() for r in caplog.records)


def test_a_pickup_within_50_ft_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    c, (a, _b) = setup([0, 1])
    did = drop(c, pos=WirePos(0, 0, 100, 30, 149))  # 49 ft from the pod
    with caplog.at_level(logging.INFO, logger="opencs.world"):
        pick(c, a, did)
    assert not [r for r in caplog.records if "beyond" in r.getMessage()]


# --- eject ---


def eject(c: City, pid: int, module: int = REPAIR, now: float = 5.0):  # type: ignore[no-untyped-def]
    return deliveries(c, c.client_message(pid, client.EjectRequest(module), now))


def test_an_eject_drops_the_module_15_ft_above_the_pod_with_vy_1_for_everyone() -> None:
    c, (a, b) = setup([0, 1])
    out = eject(c, a)
    (spawn,) = sends(out, b, server.SpawnModule)
    assert spawn.module == REPAIR and spawn.vy == 1
    placed = c.player(a).pod.placed  # type: ignore[union-attr]
    assert (spawn.pos.world_x, spawn.pos.world_z) == (placed.pos.world_x, placed.pos.world_z)
    assert spawn.pos.y == placed.pos.y + 15
    assert sends(out, a, server.SpawnModule) == [spawn]


def test_the_horizontal_kick_cycles_through_four_directions() -> None:
    c, (a, _b) = setup([0, 1])
    kicks = []
    for i in range(6):
        (spawn,) = sends(eject(c, a, now=5.0 + i), a, server.SpawnModule)
        kicks.append((spawn.vx, spawn.vz))
    assert kicks == [(1, 0), (0, 1), (-1, 0), (0, -1), (1, 0), (0, 1)]


def test_an_eject_takes_the_module_out_of_the_ledger_and_it_can_be_picked_up_again() -> None:
    c, (a, b) = setup([0, 1])
    pick(c, a, drop(c, ROCKET_PACK))
    c.client_message(a, report(0, weight=4), 4.0)
    out = eject(c, a, ROCKET_PACK)
    assert c.player(a).loadout.carried == []  # type: ignore[union-attr]
    (spawn,) = sends(out, b, server.SpawnModule)
    again = pick(c, b, spawn.id, now=6.0)
    assert to(again, b) == [server.GrantPickup(spawn.id)]
    assert c.player(b).loadout.carried == [ROCKET_PACK]  # type: ignore[union-attr]


def test_an_eject_of_a_non_module_or_before_the_first_report_is_ignored() -> None:
    c, (a,) = setup([0])
    assert eject(c, a, 10) == []
    c2, (x,) = seat([0], reported=False)
    assert eject(c2, x) == []


@pytest.mark.parametrize(
    ("module", "config"),
    [
        (31, {}),  # Liberator, level 4, in a level-2 city
        (REPAIR, {"banned_modules": (REPAIR,)}),
        (47, {"max_module_level": 5, "banned_modules": (47,)}),
    ],
)
def test_an_eject_of_a_module_the_city_does_not_stock_spawns_nothing(
    module: int, config: dict[str, object], caplog: pytest.LogCaptureFixture
) -> None:
    c, (a, b) = seat([0, 1], **config)
    for pid in (a, b):
        c.client_message(pid, report(0, x_off=100, z_off=100), 1.0)
    with caplog.at_level(logging.INFO, logger="opencs.world"):
        assert eject(c, a, module) == []
    assert c.drops.drops == {}
    assert "does not stock" in caplog.text


def test_drops_beyond_the_city_bound_destroy_the_oldest_drop() -> None:
    c, (_a, b) = setup([0, 1])
    first = drop(c)
    for _ in range(policy.MAX_DROPS - 1):
        drop(c)
    assert len(c.drops.drops) == policy.MAX_DROPS
    out = deliveries(c, c.drops.create(REPAIR, SPOT, 3.0))
    assert first not in c.drops.drops and len(c.drops.drops) == policy.MAX_DROPS
    assert sends(out, b, server.DestroyObject) == [server.DestroyObject(first)]


# --- used up ---


def test_used_up_removes_the_module_from_the_ledger_and_sends_nothing() -> None:
    c, (a, _b) = setup([0, 1])
    pick(c, a, drop(c, ROCKET_PACK))
    c.client_message(a, report(0, weight=4), 4.0)
    assert c.client_message(a, client.ModuleUsedUp(ROCKET_PACK), 5.0) == []
    assert c.player(a).loadout.carried == []  # type: ignore[union-attr]
    assert c.client_message(a, client.ModuleUsedUp(ROCKET_PACK), 6.0) == []  # unknown: harmless


def test_a_client_that_was_never_sent_the_drop_cannot_pick_it_up() -> None:
    c, (a, b) = seat([0, 1], reported=False)
    c.client_message(a, report(4), 1.0)  # b is still loading and holds no drop
    did = deliveries(c, c.drops.create(REPAIR, SPOT, 2.0))[0][1].id  # type: ignore[attr-defined]
    assert pick(c, b, did) == []
    assert did in c.drops.drops and c.player(b).loadout.carried == []  # type: ignore[union-attr]
