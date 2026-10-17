"""Held games: the 10-minute hold from the drop, the 3-minute typing window from the Chamber, and
the slots they reserve (II §13 item 4, D3, D8, D9)."""

from __future__ import annotations

from opencs import policy
from opencs.clock import ManualClock
from opencs.session.held import HeldGames
from opencs.world.resume import ResumeRecord


def record(
    identity: str = "acct:A", *, city: str = "B", team: int = 1, **kw: object
) -> ResumeRecord:
    fields: dict[str, object] = {
        "callsign": "A",
        "score": 10,
        "multiplier": 2,
        "bay_points": 3,
        "modules": (),
        "dropped_at": 100.0,
        "eligible": True,
    }
    fields.update(kw)
    return ResumeRecord(identity=identity, city=city, team=team, **fields)  # type: ignore[arg-type]


def make(start: float = 100.0) -> tuple[HeldGames, ManualClock]:
    clock = ManualClock(start)
    return HeldGames(clock), clock


def test_a_record_is_held_for_ten_minutes_from_the_drop() -> None:
    held, clock = make()
    held.hold(record())
    clock.advance(policy.HOLD_SECONDS - 0.01)
    assert held.get("acct:A") is not None
    assert held.seconds_left("acct:A") > 0
    clock.advance(0.01)
    assert held.get("acct:A") is None and held.seconds_left("acct:A") == 0.0


def test_the_hold_is_ten_minutes_and_the_typing_window_three() -> None:
    assert (policy.HOLD_SECONDS, policy.TYPE_SECONDS) == (600.0, 180.0)


def test_entering_the_chamber_starts_a_three_minute_window() -> None:
    held, clock = make()
    held.hold(record())
    clock.advance(400.0)
    assert held.entered_chamber("acct:A") is not None
    assert held.seconds_left("acct:A") == policy.TYPE_SECONDS
    clock.advance(policy.TYPE_SECONDS - 0.01)
    assert held.get("acct:A") is not None
    clock.advance(0.01)
    assert held.get("acct:A") is None


def test_the_window_runs_from_the_first_entry_only() -> None:
    held, clock = make()
    held.hold(record())
    held.entered_chamber("acct:A")
    clock.advance(100.0)
    held.entered_chamber("acct:A")  # a second look does not extend it
    assert held.seconds_left("acct:A") == policy.TYPE_SECONDS - 100.0


def test_a_player_who_comes_back_late_but_inside_the_hold_still_gets_its_three_minutes() -> None:
    held, clock = make()
    held.hold(record())
    clock.advance(595.0)
    held.entered_chamber("acct:A")
    clock.advance(170.0)  # past ten minutes from the drop, inside three from the entry
    assert held.get("acct:A") is not None


def test_a_record_not_entered_for_is_gone_at_ten_minutes_whatever_else() -> None:
    held, clock = make()
    held.hold(record())
    assert held.entered_chamber("acct:NOBODY") is None
    clock.advance(policy.HOLD_SECONDS)
    assert held.entered_chamber("acct:A") is None


def test_a_newer_drop_replaces_the_older_and_restarts_the_clock() -> None:
    held, clock = make()
    held.hold(record(score=1))
    held.entered_chamber("acct:A")
    clock.advance(50.0)
    held.hold(record(score=2, dropped_at=150.0))
    got = held.get("acct:A")
    assert got is not None and got.score == 2
    assert held.seconds_left("acct:A") == policy.HOLD_SECONDS  # no window running yet


def test_discard_gives_the_record_back_once() -> None:
    held, _ = make()
    held.hold(record())
    assert held.discard("acct:A") is not None
    assert held.discard("acct:A") is None and held.get("acct:A") is None


def test_slots_are_the_eligible_live_records_of_that_city() -> None:
    held, clock = make()
    held.hold(record("acct:A", city="B", team=1))
    held.hold(record("acct:C", city="B", team=3))
    held.hold(record("acct:D", city="A", team=0))
    held.hold(record("acct:E", city="B", team=2, eligible=False, reason="X"))
    assert sorted(held.slots("B")) == [("acct:A", 1), ("acct:C", 3)]
    assert held.slots("A") == [("acct:D", 0)] and held.slots("C") == []
    clock.advance(policy.HOLD_SECONDS)
    assert held.slots("B") == []


def test_a_record_that_is_not_eligible_is_still_kept_to_explain_the_refusal() -> None:
    held, _ = make()
    held.hold(record(eligible=False, reason="DAMAGE"))
    got = held.get("acct:A")
    assert got is not None and got.reason == "DAMAGE"
