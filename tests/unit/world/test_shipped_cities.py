"""The shipped cities, built as the server builds them: their NPCs and weather run (II §16, II
§5)."""

from __future__ import annotations

import random
from pathlib import Path

from fixtures.delivery import deliveries, to
from fixtures.pods import report
from opencs import policy
from opencs.config import load_config
from opencs.messages import server
from opencs.world.city import City

SHIPPED = Path(__file__).parents[3] / "config" / "server.toml"


def started(letter: str) -> tuple[City, int]:
    config = {c.letter: c for c in load_config(SHIPPED).cities}[letter]
    c = City(config, random.Random(5))
    r = c.join("ALPHA", 0, 0.0)
    assert hasattr(r, "player_id")
    pid: int = r.player_id  # type: ignore[union-attr]
    c.client_message(pid, report(4), 0.5)
    return c, pid


def ambient(c: City, pid: int, now: float) -> list[tuple[int, int]]:
    return [
        (m.sub, m.arg) for m in to(deliveries(c, c.tick(now)), pid) if isinstance(m, server.Ambient)
    ]


def test_city_a_has_no_npcs_and_no_weather() -> None:
    c, pid = started("A")
    assert not c.sentinels.alive and not c.munchers.alive
    assert ambient(c, pid, 100_000.0) == []


def test_city_b_stands_two_sentinels_and_rains_on_schedule() -> None:
    c, pid = started("B")
    assert sorted(c.sentinels.alive) == [policy.SENTINEL_IDS[0], policy.SENTINEL_IDS[0] + 1]
    assert ambient(c, pid, 599.9) == []
    assert ambient(c, pid, 600.0) == [(3, 2)]  # rain 2 by ramp, 600 s into the game
    assert ambient(c, pid, 900.0) == [(3, 0)]
    assert ambient(c, pid, 1500.0) == [(3, 2)]  # and round again


def test_city_c_stands_four_sentinels_dims_storms_clears_and_brightens() -> None:
    c, pid = started("C")
    assert len(c.sentinels.alive) == 4
    assert ambient(c, pid, 300.0) == [(1, 3)]
    assert ambient(c, pid, 600.0) == [(3, 3), (4, 1)]
    assert ambient(c, pid, 840.0) == [(3, 0), (4, 0)]
    assert ambient(c, pid, 1140.0) == [(1, 0)]


def test_city_c_sends_a_free_muncher_every_ten_minutes_at_the_leading_team() -> None:
    c, pid = started("C")
    tower = c.orders.place_tower(1, report(0).pos, False, 5.0)
    assert tower  # the towers' 'M' reached the one client
    deliveries(c, c.tick(599.9))
    assert not c.munchers.alive
    out = deliveries(c, c.tick(600.0))
    assert [m for m in to(out, pid) if isinstance(m, server.MobileCreate)]
    assert len(c.munchers.alive) == 1
