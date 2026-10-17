"""Records saved from a session's reports and restored at the next one (I §9.2.11, II §11.3, II
§13).

Everything goes through a real ``Session`` and the persistence layer into a memory store, so the
test sees what a later session would load."""

from __future__ import annotations

import logging

import pytest

from fixtures.pods import report
from fixtures.sessions import Harness, World, enter_lobby, make_world
from opencs import policy
from opencs.clock import ManualClock
from opencs.messages import ClientMessage, ServerMessage, client, client_payload, server
from opencs.session.persistence import Persistence, to_record
from opencs.store.base import ProgressRecord
from opencs.store.memory import MemoryProgressStore
from opencs.wire.frame import build_frame
from opencs.world.progress import SavedProgress

BAYS = (3, 2, 1, 1, 5, 4, 0, 1, 2)
STORED = ProgressRecord(bays=BAYS, marker=-2, bay_points=957, score=1670, career=500)
ACCOUNT = "ALICE,secret"
KEY = "acct:ALICE"


def frame(msg: ClientMessage) -> bytes:
    return build_frame(client_payload(msg), terminator=b"\x0a")


def start(world: World, account: str = ACCOUNT) -> Harness:
    """A session taken through ``/PLAY A`` into the game, its first ``'b'`` not yet sent."""
    h = enter_lobby(world, account)
    h.feed("/PLAY A RED\r/GO 0\r")
    h.take()
    return h


def land(h: Harness) -> list[ServerMessage]:
    """Send the first ``'b'`` and return what the server sent from then on."""
    h.io.messages.clear()
    h.feed(frame(report(4)))
    return list(h.io.messages)


def saved(world: World, key: str = KEY) -> ProgressRecord:
    record = world.progress.load(key)
    assert record is not None
    return record


def world_with(record: ProgressRecord | None = STORED, key: str = KEY) -> World:
    world = make_world()
    if record is not None:
        world.progress.save(key, record)
    return world


# --- restore ----------------------------------------------------------------------------------


def test_the_session_starts_with_bays_then_status_from_the_record() -> None:
    world = world_with()
    h = start(world)
    out = [m for m in land(h) if isinstance(m, server.SetBays | server.SetStatus)]
    floor = world.services.cities["A"].multiplier_floor
    # Bays first (the client's progression never runs without them), SCORE 0 for the new game,
    # the saved bay points, and the city floor as the multiplier.
    assert out == [server.SetBays(BAYS, -2), server.SetStatus(0, floor, 957)]


def test_a_new_identity_starts_from_the_defaults() -> None:
    h = start(world_with(None))
    out = [m for m in land(h) if isinstance(m, server.SetBays | server.SetStatus)]
    assert server.SetBays(policy.NEW_PLAYER_BAYS, policy.NEW_PLAYER_MARKER) == out[0]
    assert isinstance(out[1], server.SetStatus) and (out[1].score, out[1].bay_points) == (0, 0)


def test_a_new_game_rolls_the_last_score_into_the_career_in_the_store() -> None:
    world = world_with()
    start(world)
    now = saved(world)
    assert (now.score, now.career) == (0, 2170)
    assert (now.bays, now.marker, now.bay_points) == (BAYS, -2, 957)


def test_the_stored_record_is_unchanged_by_a_session_that_reports_nothing_new() -> None:
    world = world_with(ProgressRecord(BAYS, -2, 957, 0, 500))
    h = start(world)
    land(h)
    assert saved(world).career == 500 and world.progress.load(KEY) is not None


def test_guests_are_keyed_by_their_callsign_in_connection_order() -> None:
    world = make_world()
    a, b = start(world, "JOINGENIE"), start(world, "JOINGENIE")
    for h in (a, b):
        land(h)
    a.feed(frame(client.Bays((2, 2, 2, 2, 0, 0, 0, 0, 0), 0)))
    b.feed(frame(client.Bays((4, 4, 4, 4, 0, 0, 0, 0, 0), 0)))
    assert saved(world, "JOINGENI").bays[0] == 2 and saved(world, "JOINGEN2").bays[0] == 4


# --- saving -----------------------------------------------------------------------------------


def test_reports_are_saved_as_they_arrive() -> None:
    world = world_with()
    h = start(world)
    land(h)
    h.feed(frame(client.Status(40, 3, 1_100)))
    h.feed(frame(client.Bays((3, 2, 1, 1, 5, 4, 1, 1, 2), 0)))
    now = saved(world)
    assert (now.score, now.bay_points) == (40, 1_100)
    assert (now.bays, now.marker) == ((3, 2, 1, 1, 5, 4, 1, 1, 2), 0)


def test_a_score_below_the_last_one_saved_is_not_saved() -> None:
    world = world_with()
    h = start(world)
    land(h)
    h.feed(frame(client.Status(500, 2, 1_000)))
    h.feed(frame(client.Status(499, 2, 9_000)))
    assert (saved(world).score, saved(world).bay_points) == (500, 1_000)


def test_a_report_built_before_the_restore_is_held_until_the_settle() -> None:
    world = world_with()
    h = start(world)
    land(h)
    h.feed(frame(client.Status(0, 1, 0)))
    assert saved(world).bay_points == 957
    h.advance(policy.RESTORE_SETTLE - 0.5)
    h.feed(frame(client.Status(0, 1, 0)))
    assert saved(world).bay_points == 957
    h.advance(0.5)
    h.feed(frame(client.Status(0, 1, 0)))
    assert saved(world).bay_points == 0


def test_a_death_saves_the_marker_after_the_clients_minus_one() -> None:
    world = world_with()
    h = start(world)
    land(h)
    h.feed(frame(client.Bays(BAYS, 2)))
    h.feed(frame(report(0)))
    h.feed(frame(client.Died()))
    assert saved(world).marker == 1


def test_a_bay_bought_with_points_saves_the_points_spent() -> None:
    world = world_with(ProgressRecord((1, 1, 1, 1, 0, 0, 0, 0, 0), 0, 0, 0, 0))
    h = start(world)
    land(h)
    h.feed(frame(client.Status(10, 1, 957)))
    h.feed(frame(client.Bays((1, 1, 1, 1, 1, 0, 0, 0, 0), 0)))
    now = saved(world)
    assert now.bays[4] == 1 and now.bay_points == 0


def test_the_quit_handshake_is_a_final_save() -> None:
    world = world_with()
    h = start(world)
    land(h)
    # What Shift-Q sends, built from live values (II §11.3): the first status is built
    # before the bays, the last after.
    h.feed(frame(client.Status(300, 2, 1_000)))
    h.feed(frame(client.Bays((4, 2, 1, 1, 5, 4, 0, 1, 2), 0)))
    h.feed(frame(client.Status(320, 2, 1_020)))
    h.feed(frame(client.Quit()))
    now = saved(world)
    assert (now.score, now.bay_points, now.bays[0]) == (320, 1_020, 4)


def test_a_line_drop_keeps_what_was_reported_before_it() -> None:
    world = world_with()
    h = start(world)
    land(h)
    h.feed(frame(client.Status(77, 2, 1_099)))
    h.session.closed()
    assert (saved(world).score, saved(world).bay_points) == (77, 1_099)


# --- the next session -------------------------------------------------------------------------


def test_the_next_session_restores_what_the_last_one_reported() -> None:
    world = world_with()
    h = start(world)
    land(h)
    h.feed(frame(client.Status(900, 3, 1_234)))
    h.feed(frame(client.Bays((5, 5, 5, 5, 5, 5, 5, 5, 5), 4)))
    h.session.closed()
    again = start(world)
    out = [m for m in land(again) if isinstance(m, server.SetBays | server.SetStatus)]
    assert out[0] == server.SetBays((5,) * 9, 4)
    assert isinstance(out[1], server.SetStatus)
    assert (out[1].score, out[1].bay_points) == (0, 1_234)  # SCORE is per game
    assert saved(world).career == 2170 + 900


# --- failures ---------------------------------------------------------------------------------


class Flaky(MemoryProgressStore):
    """A progress store that fails on demand."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_loads = False
        self.fail_saves = 0

    def load(self, identity: str) -> ProgressRecord | None:
        if self.fail_loads:
            raise OSError("disk gone")
        return super().load(identity)

    def save(self, identity: str, record: ProgressRecord) -> None:
        if self.fail_saves:
            self.fail_saves -= 1
            raise OSError("disk full")
        super().save(identity, record)


def test_a_record_that_cannot_be_read_is_not_overwritten_by_the_defaults(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = Flaky()
    store.save(KEY, STORED)
    world = make_world(progress=store)
    store.fail_loads = True
    with caplog.at_level(logging.ERROR):
        h = start(world)
    store.fail_loads = False
    land(h)
    h.feed(frame(client.Status(5, 1, 5)))
    h.feed(frame(client.Bays((1, 1, 1, 1, 0, 0, 0, 0, 0), 0)))
    h.session.closed()
    assert saved(world).bays == BAYS and saved(world).score == STORED.score  # untouched
    assert "could not read the record" in caplog.text


def test_a_failed_write_is_retried_with_the_newest_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = Flaky()
    world = make_world(progress=store)
    h = start(world)
    land(h)
    store.fail_saves = 1
    with caplog.at_level(logging.ERROR):
        h.feed(frame(client.Status(10, 1, 20)))  # fails
    assert world.progress.load(KEY) is None
    world.clock.advance(policy.SAVE_RETRY_INTERVAL)
    h.feed(frame(client.Status(30, 1, 40)))  # the retry writes this one, not the lost one
    now = saved(world)
    assert (now.score, now.bay_points) == (30, 40)
    assert "could not save" in caplog.text


def test_a_failed_write_is_retried_when_nothing_new_changed() -> None:
    store = Flaky()
    world = make_world(progress=store)
    h = start(world)
    land(h)
    store.fail_saves = 1
    h.feed(frame(client.Status(10, 1, 20)))  # fails
    world.clock.advance(policy.SAVE_RETRY_INTERVAL)
    h.feed(frame(client.Status(10, 1, 20)))  # the same report: nothing new, the waiting one lands
    assert (saved(world).score, saved(world).bay_points) == (10, 20)


def test_a_failing_store_is_not_tried_on_every_message() -> None:
    store = Flaky()
    world = make_world(progress=store)
    h = start(world)
    land(h)
    store.fail_saves = 1
    h.feed(frame(client.Status(10, 1, 20)))  # fails, and the wait begins
    h.feed(frame(client.Status(30, 1, 40)))
    h.feed(frame(client.Status(50, 1, 60)))
    assert world.progress.load(KEY) is None  # no attempt was made inside the wait
    world.clock.advance(policy.SAVE_RETRY_INTERVAL)
    h.feed(frame(client.Status(70, 1, 80)))
    assert (saved(world).score, saved(world).bay_points) == (70, 80)


def test_score_is_answered_from_the_record_waiting_for_a_retry() -> None:
    store = Flaky()
    world = make_world(progress=store)
    h = start(world)
    land(h)
    store.fail_saves = 1
    h.feed(frame(client.Status(10, 1, 20)))  # fails: the store has nothing
    waiting = world.services.persistence.record(KEY)
    assert waiting is not None and (waiting.score, waiting.bay_points) == (10, 20)
    assert world.progress.load(KEY) is None


def test_save_all_writes_the_record_of_every_player_seated() -> None:
    world = make_world()
    h = start(world)
    land(h)
    h.feed(frame(client.Status(8, 1, 9)))
    world.progress.save(KEY, ProgressRecord((0,) * 9, 0, 0, 0, 0))  # lost or stale
    world.services.persistence.save_all(world.services.cities.values())
    assert (saved(world).bays, saved(world).score) == (policy.NEW_PLAYER_BAYS, 8)


def test_persistence_converts_between_the_store_and_the_world() -> None:
    record = SavedProgress(BAYS, -2, 957, 1, 2)
    assert to_record(record) == ProgressRecord(BAYS, -2, 957, 1, 2)
    p = Persistence(MemoryProgressStore(), ManualClock())
    assert p.load("nobody") is None
