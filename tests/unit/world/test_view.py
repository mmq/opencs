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
