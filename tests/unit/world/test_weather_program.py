"""A city's weather program: steps when due, transitions to those present, the condition at once
to a joiner, and palette changes kept 50 s apart (I §8.20, I §7.3, II §5)."""

from __future__ import annotations

from fixtures.cities import seat
from fixtures.delivery import deliveries, to
from fixtures.lobbies import city
from fixtures.pods import report
from opencs import policy
from opencs.config import AmbientStep
from opencs.messages import ServerMessage, client, server
from opencs.world.city import City
from opencs.world.weather import Weather

STORM = (AmbientStep(100, light=3), AmbientStep(200, rain=3, lightning=True))
#: The shipped city C's program.
CITY_C = (
    AmbientStep(300, light=3),
    AmbientStep(300, rain=3, lightning=True),
    AmbientStep(240, rain=0, lightning=False),
    AmbientStep(300, light=0),
)


def ambient(messages: list[ServerMessage]) -> list[tuple[int, int]]:
    return [(m.sub, m.arg) for m in messages if isinstance(m, server.Ambient)]


def run(c: City, now: float) -> list[tuple[int, ServerMessage]]:
    """What a tick sends, minus the drop's repeated ``'A'`` that a freshly seated pod is owed."""
    return [(p, m) for p, m in deliveries(c, c.tick(now)) if isinstance(m, server.Ambient)]


# --- the program on its own -------------------------------------------------------------------


def test_nothing_happens_before_the_first_step_is_due() -> None:
    w = Weather(STORM)
    w.start(10.0)
    assert w.tick(109.9) == []
    assert w.condition == (0, 0, 0)


def test_a_step_applies_at_its_time_and_the_program_loops() -> None:
    w = Weather(STORM)
    w.start(10.0)
    assert w.tick(110.0) == [((0, 0, 0), (3, 0, 0))]
    assert w.tick(309.9) == []
    assert w.tick(310.0) == [((3, 0, 0), (3, 3, 1))]
    # The loop: the first step again 100 s later, which changes nothing (palette 3 already).
    assert w.tick(410.0) == [((3, 3, 1), (3, 3, 1))]


def test_a_tick_after_a_long_gap_applies_every_step_that_fell_due() -> None:
    w = Weather(CITY_C)
    w.start(0.0)
    steps = w.tick(1140.0)  # steps due at 300, 600, 840 and 1140
    # The last changes the light, which was changed (late) in this very tick: it waits 50 s.
    assert [after for _, after in steps] == [(3, 0, 0), (3, 3, 1), (3, 0, 0)]
    assert w.tick(1189.9) == []
    assert [after for _, after in w.tick(1190.0)] == [(0, 0, 0)]


def test_a_program_that_is_not_started_does_not_run() -> None:
    w = Weather(STORM)
    assert w.tick(1e6) == []


def test_reset_stops_the_program_and_restores_the_default_weather() -> None:
    w = Weather(STORM)
    w.start(0.0)
    w.tick(500.0)
    w.reset()
    assert w.condition == (policy.DEFAULT_PALETTE, 0, 0)
    assert w.tick(1e6) == []


# --- what the clients are sent ---------------------------------------------------------------


def test_a_present_client_is_sent_transitions_not_the_condition() -> None:
    c, (a, b) = seat([0, 1], ambient=CITY_C)
    assert run(c, 299.9) == []
    out = run(c, 300.0)
    assert ambient(to(out, a)) == [(1, 3)]  # palette 3 by crossfade, not sub 0
    assert ambient(to(out, b)) == [(1, 3)]
    out = run(c, 600.0)
    assert ambient(to(out, a)) == [(3, 3), (4, 1)]  # rain 3 by ramp, lightning on
    out = run(c, 840.0)
    assert ambient(to(out, a)) == [(3, 0), (4, 0)]


def test_only_what_changed_is_sent() -> None:
    c, (a,) = seat([0], ambient=(AmbientStep(10, rain=1), AmbientStep(10, rain=1, lightning=True)))
    assert ambient(to(run(c, 10.0), a)) == [(3, 1)]
    assert ambient(to(run(c, 20.0), a)) == [(4, 1)]  # rain was already 1


def test_a_step_that_names_the_current_palette_sends_no_crossfade() -> None:
    c, (a,) = seat([0], ambient=(AmbientStep(10, light=0),))
    assert ambient(to(run(c, 10.0), a)) == []


def test_a_joiner_mid_storm_is_sent_the_condition_at_once() -> None:
    c, _ = seat([0], ambient=CITY_C)
    run(c, 600.0)  # dim light and the storm
    late = c.join("LATE", 1, 610.0)
    assert hasattr(late, "player_id")
    out = deliveries(c, c.client_message(late.player_id, report(4), 611.0))  # type: ignore[union-attr]
    assert ambient(to(out, late.player_id)) == [(0, 3), (2, 3), (4, 1)]  # type: ignore[union-attr]
    # Later changes reach it as transitions like everyone.
    out = run(c, 840.0)
    assert ambient(to(out, late.player_id)) == [(3, 0), (4, 0)]  # type: ignore[union-attr]


def test_a_client_still_loading_is_not_sent_a_step_but_is_given_the_condition_later() -> None:
    c, _ = seat([0], ambient=CITY_C)
    r = c.join("LOAD", 1, 100.0)
    assert hasattr(r, "player_id")
    pid = r.player_id  # type: ignore[union-attr]
    assert to(run(c, 300.0), pid) == []
    out = deliveries(c, c.client_message(pid, report(4), 301.0))
    assert ambient(to(out, pid)) == [(0, 3)]


def test_the_game_clock_starts_with_the_first_player_not_the_city() -> None:
    c = City(city("A", ambient=STORM))
    assert run(c, 1e6) == []  # nobody here: nothing runs
    first = c.join("A", 0, 1000.0)
    assert hasattr(first, "player_id")
    c.client_message(first.player_id, report(4), 1000.5)  # type: ignore[union-attr]
    assert run(c, 1099.0) == []
    assert ambient([m for _, m in run(c, 1100.0)]) == [(1, 3)]


def test_the_program_starts_over_after_the_city_empties() -> None:
    c, (a,) = seat([0], ambient=STORM)
    run(c, 150.0)
    assert c.weather.palette == 3
    c.leave(a, 160.0)
    r = c.join("NEW", 0, 1000.0)
    assert hasattr(r, "player_id")
    assert c.weather.palette == 0
    c.client_message(r.player_id, report(4), 1000.5)  # type: ignore[union-attr]
    assert run(c, 1099.0) == []
    assert ambient([m for _, m in run(c, 1100.0)]) == [(1, 3)]


# --- palette changes stay 50 s apart ------------------------------------------------------


def test_the_weather_module_is_ignored_while_a_program_crossfade_runs() -> None:
    c, (a,) = seat([0], ambient=(AmbientStep(100, light=3),), max_module_level=5)
    run(c, 100.0)  # the crossfade begins
    out = deliveries(c, c.client_message(a, client.EffectRequest("W"), 149.0))
    assert out == [] and c.weather.palette == 3
    out = deliveries(c, c.client_message(a, client.EffectRequest("W"), 150.0))
    assert ambient(to(out, a)) == [(0, 4)] and c.weather.palette == 4


def test_a_light_step_waits_until_the_last_palette_change_is_50_seconds_old() -> None:
    c, (a,) = seat([0], ambient=(AmbientStep(100, light=3),), max_module_level=5)
    c.client_message(a, client.EffectRequest("W"), 80.0)  # palette 1 at 80
    assert run(c, 100.0) == []  # due, but only 20 s after the module's change
    assert run(c, 129.9) == []
    assert ambient(to(run(c, 130.0), a)) == [(1, 3)]


def test_the_steps_behind_a_waiting_light_step_wait_with_it() -> None:
    c, (a,) = seat(
        [0],
        ambient=(AmbientStep(100, light=3), AmbientStep(5, rain=2)),
        max_module_level=5,
    )
    c.client_message(a, client.EffectRequest("W"), 80.0)
    assert run(c, 110.0) == []
    out = run(c, 130.0)
    assert ambient(to(out, a)) == [(1, 3), (3, 2)]  # in order, in one tick


def test_two_light_steps_a_valid_55_seconds_apart_are_both_applied_on_time() -> None:
    c, (a,) = seat([0], ambient=(AmbientStep(100, light=3), AmbientStep(55, light=1)))
    assert ambient(to(run(c, 100.0), a)) == [(1, 3)]
    assert ambient(to(run(c, 155.0), a)) == [(1, 1)]
