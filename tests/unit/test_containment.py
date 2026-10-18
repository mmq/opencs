"""No exception escapes a session or a city call, and one connection's failure touches no other
(DESIGN §5 "Bugs vs bad input")."""

from __future__ import annotations

import logging

import pytest

from fixtures.pods import report
from fixtures.sessions import World, make_world, play
from opencs import policy
from opencs.messages import ClientMessage, ServerMessage, client, client_payload, server
from opencs.wire.frame import build_frame
from opencs.world.city import City
from opencs.world.events import Event, ToAll


def frame(msg: ClientMessage) -> bytes:
    return build_frame(client_payload(msg), terminator=b"\x0a")


def city_a(world: World) -> City:
    return world.services.cities["A"]


def test_a_city_bug_on_one_players_message_leaves_the_other_player_and_the_city_running(
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = make_world()
    bad = play(world, "A", 0)
    good = play(world, "A", 1, account="OTHER")
    city = city_a(world)
    real = city.client_message
    bad_id = city.players()[0].id

    def flaky(player_id: int, msg: ClientMessage, now: float) -> list:  # type: ignore[type-arg]
        if player_id == bad_id and isinstance(msg, client.PodState):
            raise RuntimeError("rule bug")
        return real(player_id, msg, now)

    city.client_message = flaky  # type: ignore[method-assign]
    good.io.messages.clear()
    with caplog.at_level(logging.ERROR):
        bad.feed(frame(report(4)))
    assert not bad.io.closed and not good.io.closed
    # Logged with the request and the city, so the bug can be reproduced.
    assert any(
        "city A failed on" in r.getMessage() and "PodState" in r.getMessage()
        for r in caplog.records
    )
    # The other player carries on and the city still ticks.
    good.feed(frame(report(4)))
    assert any(isinstance(m, server.ReleaseDrop) for m in good.io.messages)
    world.services.router.deliver(city, city.tick(world.clock.now() + 1))
    assert len(city.players()) == 2


def test_a_session_handler_that_raises_closes_only_its_own_connection() -> None:
    world = make_world()
    bad = play(world, "A", 0)
    good = play(world, "A", 1, account="OTHER")

    def boom(data: bytes) -> None:
        raise RuntimeError("phase bug")

    bad.session.phase.feed = boom  # type: ignore[method-assign]
    bad.feed(b"x")
    assert bad.io.closed_with == ["internal error"]
    assert not good.io.closed
    good.feed(frame(report(4)))
    assert any(isinstance(m, server.ReleaseDrop) for m in good.io.messages)


def test_a_tick_that_raises_in_one_session_does_not_stop_the_others_ticking() -> None:
    world = make_world()
    bad = play(world, "A", 0)
    good = play(world, "A", 1, account="OTHER")

    def boom(now: float) -> None:
        raise RuntimeError("tick bug")

    bad.session.phase.tick = boom  # type: ignore[method-assign]
    bad.advance(1)
    assert bad.io.closed
    good.advance(policy.B_REPEAT_INTERVAL * 2)
    assert not good.io.closed
    assert any(isinstance(m, server.Session) for m in good.io.messages)


def test_a_sink_that_raises_does_not_stop_delivery_to_the_rest(
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = make_world()
    play(world, "A", 0)
    b = play(world, "A", 1, account="OTHER")
    city = city_a(world)
    first = city.players()[0].id

    class Broken:
        def send(self, msg: ServerMessage) -> None:
            raise RuntimeError("sink bug")

    world.services.router.register("A", first, Broken())
    b.io.messages.clear()
    with caplog.at_level(logging.ERROR):
        world.services.router.deliver(city, [Event(server.ReleaseDrop(0), ToAll())])
    assert b.io.messages == [server.ReleaseDrop(0)]
    assert any("delivery to player" in r.getMessage() for r in caplog.records)
