"""The multiplier the client has spent on tower orders, as the mirror follows it (I §9.4, II §13
item 2): a tower costs 1, a MegaTower 2, every refusal refunds 1, a ``'D'``-``'S'`` resets it."""

from __future__ import annotations

from fixtures.cities import seat
from fixtures.pods import report
from opencs import policy
from opencs.messages import WirePos, client
from opencs.world.city import City
from opencs.world.player import Player

HOME = WirePos.from_world(300, 20, 300)  # team 0's own corner: never a refusal
ENEMY_HOME = WirePos.from_world(4500, 20, 300)  # inside team 1's 3x3 no-drop zone for team 0


def setup(multiplier: int = 6) -> tuple[City, Player]:
    c, (a,) = seat([0])
    c.client_message(a, report(0), 1.0)
    c.client_message(a, client.Status(100, multiplier, 50), 2.0)
    p = c.player(a)
    assert p is not None
    return c, p


def test_a_tower_spends_one_and_a_mega_tower_two() -> None:
    c, p = setup()
    c.client_message(p.id, client.TowerOrder(HOME, 0, 0), 3.0)
    assert (p.score.tower_spent, p.score.live_multiplier) == (1, 5)
    c.client_message(p.id, client.TowerOrder(HOME, 1, 0), 3.5)
    assert (p.score.tower_spent, p.score.live_multiplier) == (3, 3)


def test_a_refused_tower_costs_nothing() -> None:
    c, p = setup()
    c.client_message(p.id, client.TowerOrder(ENEMY_HOME, 0, 0), 3.0)  # 'O' 1: refund
    assert p.score.tower_spent == 0


def test_a_refused_mega_tower_still_costs_one_of_its_two() -> None:
    c, p = setup()
    c.client_message(p.id, client.TowerOrder(ENEMY_HOME, 1, 0), 3.0)
    assert (p.score.tower_spent, p.score.live_multiplier) == (1, 5)


def test_a_queue_full_refusal_refunds_too() -> None:
    c, p = setup()
    for i in range(policy.MAX_WAITING_TOWER_ORDERS + 3):
        c.client_message(p.id, client.TowerOrder(HOME, 0, 0), 3.0 + i / 10)
    accepted = p.score.tower_spent
    assert accepted < policy.MAX_WAITING_TOWER_ORDERS + 3  # some were refused and refunded
    assert accepted >= policy.MAX_WAITING_TOWER_ORDERS


def test_an_order_the_session_dropped_over_budget_nets_zero() -> None:
    c, p = setup()
    c.refuse_order(p.id, client.TowerOrder(HOME, 0, 0))  # spent, then 'O' 2 refunds it
    assert p.score.tower_spent == 0
    c.refuse_order(p.id, client.ModuleOrder(1, 1, 0))  # a module costs no multiplier
    assert p.score.tower_spent == 0


def test_the_next_status_report_replaces_the_estimate() -> None:
    c, p = setup()
    c.client_message(p.id, client.TowerOrder(HOME, 0, 0), 3.0)
    c.client_message(p.id, client.Status(120, 4, 60), 10.0)
    assert (p.score.tower_spent, p.score.live_multiplier) == (0, 4)


def test_the_estimate_is_never_below_one_nor_raised_by_a_stray_refund() -> None:
    c, p = setup(multiplier=2)
    for i in range(4):
        c.client_message(p.id, client.TowerOrder(HOME, 1, 0), 3.0 + i)
    assert p.score.live_multiplier == 1
    p.score.tower_spent = -3  # refunds for orders counted before a report
    assert p.score.live_multiplier == 2  # the mirror's own value, never more


def test_a_death_resets_the_client_and_the_estimate() -> None:
    c, p = setup()
    c.client_message(p.id, client.TowerOrder(HOME, 0, 0), 3.0)
    c.client_message(p.id, client.Died(), 4.0)
    assert (p.score.tower_spent, p.score.live_multiplier) == (0, 1)
