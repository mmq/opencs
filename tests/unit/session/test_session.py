"""The session shell: no exception escapes; closing releases everything (DESIGN §5)."""

from __future__ import annotations

from fixtures.sessions import connect, enter_lobby, make_world


def test_an_exception_in_a_phase_closes_that_connection_and_does_not_escape() -> None:
    world = make_world()
    good = enter_lobby(world, "ALPHA")
    h = enter_lobby(world, "BRAVO")

    def boom(data: bytes) -> None:
        raise RuntimeError("bug")

    h.session.phase.feed = boom  # type: ignore[method-assign]
    h.feed(b"anything")  # must not raise
    assert h.io.closed_with == ["internal error"]
    h.feed(b"more")  # ignored once closing
    assert not good.io.closed


def test_an_exception_in_tick_closes_the_connection() -> None:
    h = connect(make_world())

    def boom(now: float) -> None:
        raise RuntimeError("bug")

    h.session.phase.tick = boom  # type: ignore[method-assign]
    h.advance(1)
    assert h.io.closed_with == ["internal error"]


def test_closed_removes_the_member_and_frees_the_callsign_even_after_an_error() -> None:
    world = make_world()
    h = enter_lobby(world)
    assert world.lobby.find("JOINGENI") is not None
    h.session.closed()
    assert world.lobby.find("JOINGENI") is None
    assert not world.identity.callsign_online("JOINGENI")
