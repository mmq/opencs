"""The ``'T'``-``'I'`` grid reaches every client once, on its first report (I §7.3, I §8.22)."""

from __future__ import annotations

from fixtures.cities import seat
from fixtures.delivery import deliveries, to
from fixtures.pods import report
from opencs.messages import server

BLOCKS = tuple(tuple((x + 2 * z) % 4 for x in range(8)) for z in range(8))


def grids(messages: list[object]) -> list[server.SetBlocks]:
    return [m for m in messages if isinstance(m, server.SetBlocks)]


def test_each_client_gets_the_grid_once_on_its_first_report() -> None:
    c, (a, b) = seat([0, 1], reported=False, blocks=BLOCKS)
    first = deliveries(c, c.client_message(a, report(4), 1.0))
    assert len(grids(to(first, a))) == 1 and grids(to(first, b)) == []
    second = deliveries(c, c.client_message(b, report(4), 1.5))
    assert len(grids(to(second, b))) == 1 and grids(to(second, a)) == []
    # It is city-wide state, not an entity: the client holds no id for it.
    assert all(i >= 0 for i in c.player(a).view.held) and c.player(a).view.told
    # A later report, the relay of 'b' and a walk never repeat it.
    later = deliveries(c, c.client_message(a, report(0), 2.0))
    assert grids([m for _, m in later]) == []
    assert grids(to(first, a))[0].classes == tuple(BLOCKS[z][x] for x in range(8) for z in range(8))


def test_a_city_with_no_map_sends_no_grid_and_holds_nothing_for_it() -> None:
    c, (a,) = seat([0], reported=False)
    out = deliveries(c, c.client_message(a, report(4), 1.0))
    assert grids([m for _, m in out]) == []
    assert c.player(a).view.held == {a}  # type: ignore[union-attr]


def test_a_client_still_loading_is_sent_no_grid() -> None:
    c, (a, b) = seat([0, 1], reported=False, blocks=BLOCKS)
    out = deliveries(c, c.client_message(a, report(4), 1.0))
    assert to(out, b) == []
