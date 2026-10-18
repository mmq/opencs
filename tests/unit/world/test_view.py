"""A client's view: what it holds (DESIGN §3.5)."""

from __future__ import annotations

from opencs.world.view import ClientView


def test_hold_and_forget() -> None:
    view = ClientView(3, joined_at=7.5)
    assert not view.holds(3)
    view.hold(3)
    view.hold(3)  # holding twice is still holding once
    assert view.holds(3) and view.held == {3}
    view.forget(3)
    view.forget(3)  # forgetting what is not held is harmless
    assert not view.holds(3)


def test_the_join_time_is_kept_for_the_id_reuse_guard() -> None:
    assert ClientView(0, joined_at=12.0).joined_at == 12.0


def test_city_wide_state_is_recorded_apart_from_the_entities_held() -> None:
    view = ClientView(3, joined_at=0.0)
    assert not view.was_told("territory", None)
    view.tell("territory", (1, 2))
    assert view.was_told("territory", (1, 2)) and not view.was_told("territory", (1, 3))
    assert view.held == set()  # nothing but real entity ids is ever held


def test_an_id_ever_held_is_remembered_after_it_is_forgotten() -> None:
    view = ClientView(3, joined_at=0.0)
    view.hold(9)
    view.forget(9)
    assert not view.holds(9)
    assert view.has_had(9)  # the client's registry never unlinks it (I §6.2)
    assert not view.has_had(10)
