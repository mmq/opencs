"""The weather palette: cycling and the late-joiner sync (I §8.20, I §9.3.5, I §7.3)."""

from __future__ import annotations

from fixtures.cities import seat
from fixtures.delivery import deliveries, to
from fixtures.pods import report
from opencs.messages import server
from opencs.world.weather import Weather


def ambient(messages: list[object]) -> list[server.Ambient]:
    return [m for m in messages if isinstance(m, server.Ambient)]


def test_the_palette_cycles_through_five_conditions_and_wraps() -> None:
    w = Weather()
    assert [w.cycle() for _ in range(6)] == [1, 2, 3, 4, 0, 1]


def test_a_client_is_sent_nothing_while_the_weather_is_the_default() -> None:
    # A fresh client already shows palette 0 (I §8.20); the worked session sends no 'R'.
    c, (a,) = seat([0], reported=False)
    first = deliveries(c, c.client_message(a, report(4), 1.0))
    assert ambient(to(first, a)) == []


def test_a_client_is_sent_a_changed_palette_once_on_its_first_report() -> None:
    c, (a, b) = seat([0, 1], reported=False)
    c.weather.cycle()
    first = deliveries(c, c.client_message(a, report(4), 1.0))
    assert ambient(to(first, a)) == [server.Ambient(0, 1)]
    assert ambient(to(first, b)) == []
    later = deliveries(c, c.client_message(a, report(0), 2.0))
    assert ambient([m for _, m in later]) == []


def test_a_joiner_after_a_change_is_sent_the_current_palette() -> None:
    c, (a, b) = seat([0, 1], reported=False)
    c.client_message(a, report(4), 1.0)
    c.weather.cycle()
    c.weather.cycle()
    out = deliveries(c, c.client_message(b, report(4), 2.0))
    assert ambient(to(out, b)) == [server.Ambient(0, 2)]
