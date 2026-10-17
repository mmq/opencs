"""A player returning to a held game gets it back as it was (II §13 item 5): bays and marker,
then SCORE, multiplier and bay points without the city's entry floor, then the modules once the pod
has landed, highest level first."""

from __future__ import annotations

from fixtures.delivery import deliveries, to
from fixtures.lobbies import city
from fixtures.pods import report
from opencs import policy
from opencs.messages import ServerMessage, client, server
from opencs.world.city import City, JoinResult
from opencs.world.progress import SavedProgress
from opencs.world.resume import ResumeRecord

BAYS = (3, 2, 1, 1, 5, 4, 0, 1, 2)
STORED = SavedProgress(bays=BAYS, marker=-2, bay_points=957, score=1670, career=500)
KEY = "acct:ALICE"
MODULES = (31, 24, 12, 3)


def held(**kw: object) -> ResumeRecord:
    fields: dict[str, object] = {
        "identity": KEY,
        "callsign": "ALICE",
        "city": "A",
        "team": 2,
        "score": 1670,
        "multiplier": 1,
        "bay_points": 957,
        "modules": MODULES,
        "dropped_at": 0.0,
        "eligible": True,
    }
    fields.update(kw)
    return ResumeRecord(**fields)  # type: ignore[arg-type]


def resumed(
    record: ResumeRecord | None = None, stored: SavedProgress | None = STORED
) -> tuple[City, int]:
    c = City(city("A", max_multiplier=8, max_module_level=2))
    r = c.join("ALICE", 2, 0.0, identity=KEY, saved=stored, resume=record or held())
    assert isinstance(r, JoinResult)
    return c, r.player_id


def land(c: City, pid: int, at: float = 5.0) -> list[ServerMessage]:
    """The first report (state 4, the drop) and the report that lands."""
    c.client_message(pid, report(4), at)
    return to(deliveries(c, c.client_message(pid, report(0), at + 1)), pid)


def first(c: City, pid: int) -> list[ServerMessage]:
    return to(deliveries(c, c.client_message(pid, report(4), 5.0)), pid)


def test_the_restore_is_bays_then_the_held_status_without_the_floor() -> None:
    c, pid = resumed()
    out = first(c, pid)
    sent = [m for m in out if isinstance(m, server.SetBays | server.SetStatus)]
    # The city's floor is 2; the held multiplier 1 is sent as it was.
    assert sent == [server.SetBays(BAYS, -2), server.SetStatus(1670, 1, 957)]
    assert c.multiplier_floor == 2


def test_a_new_game_in_the_same_city_still_gets_score_zero_and_the_floor() -> None:
    c = City(city("A", max_multiplier=8, max_module_level=2))
    r = c.join("ALICE", 2, 0.0, identity=KEY, saved=STORED)
    assert isinstance(r, JoinResult)
    sent = [m for m in first(c, r.player_id) if isinstance(m, server.SetStatus)]
    assert sent == [server.SetStatus(0, 2, 957)]


def test_the_held_multiplier_is_restored_and_capped_at_the_cities_maximum() -> None:
    c, pid = resumed(held(multiplier=5))
    assert [m for m in first(c, pid) if isinstance(m, server.SetStatus)] == [
        server.SetStatus(1670, 5, 957)
    ]
    c, pid = resumed(held(multiplier=50))
    assert [m for m in first(c, pid) if isinstance(m, server.SetStatus)] == [
        server.SetStatus(1670, 8, 957)
    ]


def test_the_held_bay_points_replace_the_stored_ones_and_score_is_not_rolled_into_the_career() -> (
    None
):
    c, pid = resumed(held(bay_points=3), stored=SavedProgress(BAYS, -2, 300, 1670, 500))
    sent = [m for m in first(c, pid) if isinstance(m, server.SetStatus)]
    assert sent == [server.SetStatus(1670, 1, 3)]
    p = c.player(pid)
    assert p is not None and p.progress.record.career == 500  # a new game would make it 2170


def test_a_resume_changes_nothing_to_save_when_nothing_differs() -> None:
    c, _ = resumed()
    assert c.take_progress() == []


def test_modules_are_installed_highest_level_first_once_the_pod_has_landed() -> None:
    c, pid = resumed()
    assert [m for m in first(c, pid) if isinstance(m, server.InstallModule)] == []  # still dropping
    out = to(deliveries(c, c.client_message(pid, report(0), 6.0)), pid)
    assert [m for m in out if isinstance(m, server.InstallModule)] == [
        server.InstallModule(m) for m in MODULES
    ]
    again = to(deliveries(c, c.client_message(pid, report(0), 7.0)), pid)
    assert [m for m in again if isinstance(m, server.InstallModule)] == []  # once only


def test_the_modules_follow_the_status_when_the_first_report_already_lands() -> None:
    c, pid = resumed()
    out = to(deliveries(c, c.client_message(pid, report(0), 5.0)), pid)
    kinds = [type(m) for m in out if isinstance(m, server.SetStatus | server.InstallModule)]
    assert kinds == [server.SetStatus] + [server.InstallModule] * len(MODULES)


def test_a_falling_pod_has_landed_for_the_restore() -> None:
    c, pid = resumed()
    c.client_message(pid, report(4), 5.0)
    out = to(deliveries(c, c.client_message(pid, report(1), 6.0)), pid)
    assert len([m for m in out if isinstance(m, server.InstallModule)]) == len(MODULES)


def test_the_installs_go_in_the_ledger_as_pending_until_the_weight_shows_them() -> None:
    c, pid = resumed()
    land(c, pid)
    p = c.player(pid)
    assert p is not None and p.loadout.pending == list(MODULES) and p.loadout.modules == []


def test_the_bounty_is_not_restored() -> None:
    c, pid = resumed()
    land(c, pid)
    p = c.player(pid)
    assert p is not None
    assert p.bounty.value(5.5) == 0  # accrual starts at the landing
    assert p.bounty.value(106.0) == 100  # a second a unit, from the landing


def test_the_floor_applies_as_normal_after_a_later_death() -> None:
    c, pid = resumed()
    land(c, pid)
    c.client_message(pid, client.Died(), 20.0)
    later = 20.0 + policy.FLOOR_SETTLE
    out = to(deliveries(c, c.client_message(pid, client.Status(1700, 1, 960), later)), pid)
    assert out == [server.SetStatus(1700, 2, 960)]


def test_a_report_built_before_the_restore_does_not_wipe_the_held_status() -> None:
    c, pid = resumed()
    first(c, pid)
    c.client_message(pid, client.Status(0, 1, 0), 5.5)  # the client's empty image
    p = c.player(pid)
    assert p is not None and (p.progress.record.score, p.progress.record.bay_points) == (1670, 957)


def test_without_the_stored_record_there_is_nothing_to_resume_and_it_is_a_new_game() -> None:
    c, pid = resumed(stored=None)
    sent = [m for m in first(c, pid) if isinstance(m, server.SetStatus | server.InstallModule)]
    assert sent == [server.SetStatus(0, 2, 0)]
