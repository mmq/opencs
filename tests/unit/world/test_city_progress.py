"""The city mirrors a stored record at join, restores it to the client, and says what to save
(I §9.2.11, II §11.3). The world writes nothing: ``take_progress`` hands the changes to the
session."""

from __future__ import annotations

from fixtures.delivery import deliveries, to
from fixtures.lobbies import city
from fixtures.pods import report
from opencs import policy
from opencs.messages import ServerMessage, client, server
from opencs.wire.codec import BIAS88_MIN
from opencs.world.city import City, JoinResult
from opencs.world.progress import ProgressChanged, SavedProgress

BAYS = (3, 2, 1, 1, 5, 4, 0, 1, 2)
STORED = SavedProgress(bays=BAYS, marker=-2, bay_points=957, score=1670, career=500)
KEY = "acct:ALICE"


def join(stored: SavedProgress | None = STORED, key: str | None = KEY) -> tuple[City, int]:
    c = City(city("A", max_multiplier=8, max_module_level=2))
    r = c.join("ALICE", 0, 0.0, identity=key, saved=stored)
    assert isinstance(r, JoinResult)
    return c, r.player_id


def first_report(c: City, pid: int) -> list[ServerMessage]:
    """What the player's first ``'b'`` sends it."""
    return to(deliveries(c, c.client_message(pid, report(4), 5.0)), pid)


def test_the_first_report_restores_bays_then_status_in_that_order() -> None:
    c, pid = join()
    out = first_report(c, pid)
    sent = [m for m in out if isinstance(m, server.SetBays | server.SetStatus)]
    assert sent == [server.SetBays(BAYS, -2), server.SetStatus(0, c.multiplier_floor, 957)]


def test_a_new_game_restores_score_zero_not_the_last_games() -> None:
    c, pid = join()
    sent = [m for m in first_report(c, pid) if isinstance(m, server.SetStatus)]
    assert sent[0].score == 0


def test_a_new_identity_is_given_the_defaults() -> None:
    c, pid = join(None)
    out = first_report(c, pid)
    assert server.SetBays(policy.NEW_PLAYER_BAYS, policy.NEW_PLAYER_MARKER) in out


def test_a_stored_marker_the_field_cannot_carry_is_clamped_before_it_is_sent() -> None:
    c, pid = join(SavedProgress(BAYS, -5000, 0, 0, 0))
    out = first_report(c, pid)
    assert server.SetBays(BAYS, BIAS88_MIN) in out


def test_joining_reports_the_rolled_record_to_save() -> None:
    c, _ = join()
    assert c.take_progress() == [
        ProgressChanged(KEY, SavedProgress(BAYS, -2, 957, score=0, career=2170))
    ]
    assert c.take_progress() == []


def test_joining_with_nothing_to_roll_or_no_record_reports_nothing() -> None:
    assert join(SavedProgress(BAYS, 0, 5, 0, 9))[0].take_progress() == []
    assert join(None)[0].take_progress() == []


def test_a_player_with_no_identity_is_never_reported() -> None:
    c, pid = join(STORED, key=None)
    c.client_message(pid, report(4), 5.0)
    c.client_message(pid, client.Status(900, 3, 50), 20.0)
    assert c.take_progress() == [] and c.all_progress() == []


def test_a_status_report_is_reported_to_save_once() -> None:
    c, pid = join()
    c.client_message(pid, report(4), 5.0)
    c.take_progress()
    c.client_message(pid, client.Status(1700, 4, 990), 20.0)
    assert c.take_progress() == [
        ProgressChanged(KEY, SavedProgress(BAYS, -2, 990, score=1700, career=2170))
    ]
    c.client_message(pid, client.Status(1700, 5, 990), 21.0)  # nothing new in the record
    assert c.take_progress() == []


def test_only_the_newest_record_of_an_identity_is_handed_over() -> None:
    c, pid = join()
    c.client_message(pid, report(4), 5.0)
    c.client_message(pid, client.Status(1700, 4, 990), 20.0)
    c.client_message(pid, client.Status(1800, 4, 995), 25.0)
    notes = c.take_progress()
    assert [n.record.score for n in notes] == [1800]


def test_a_bay_report_is_reported_with_its_marker() -> None:
    c, pid = join()
    c.client_message(pid, report(4), 5.0)
    c.take_progress()
    c.client_message(pid, client.Bays((3, 2, 1, 1, 5, 4, 1, 1, 2), 0), 20.0)
    (note,) = c.take_progress()
    assert (note.record.bays, note.record.marker) == ((3, 2, 1, 1, 5, 4, 1, 1, 2), 0)


def test_a_bay_report_the_client_cannot_hold_is_not_saved() -> None:
    c, pid = join()
    c.take_progress()
    c.client_message(pid, client.Bays((9, 2, 1, 1, 5, 4, 0, 1, 2), 0), 20.0)
    assert c.take_progress() == []


def test_the_own_death_marker_is_reported_after_the_clients_silent_minus_one() -> None:
    c, pid = join()
    c.client_message(pid, report(4), 5.0)
    c.client_message(pid, report(0), 6.0)
    c.take_progress()
    c.client_message(pid, client.Died(), 30.0)
    (note,) = c.take_progress()
    assert note.record.marker == -3  # -2 restored, -1 for the death


def test_all_progress_lists_every_kept_player_whether_or_not_it_changed() -> None:
    c, _ = join()
    c.take_progress()
    assert c.all_progress() == [
        ProgressChanged(KEY, SavedProgress(BAYS, -2, 957, score=0, career=2170))
    ]
