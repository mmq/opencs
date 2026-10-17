"""Every captured run with ``'D'``-``'S'`` reports, replayed through the save path (II §11.3).

The fake clients never send a ``'D'``, so only real captures say what a client really reports. Each
run's reports are fed to a ``City`` whose player has an identity, with ``Persistence`` writing to a
memory store after every message, exactly as a session does. Three checks:

1. SAVED: the stored SCORE is the highest the client reported, the stored bay points come from the
   last report that had it, and the stored bays are the last ``'D'``-``'B'``. The run ends, as the
   early captures' quit handshakes did, with an all-zero report (II §11.3): it must not
   take any of that back.
2. SURVIVES A RECONNECT: a new session for the same identity restores the record, and a report
   built before the restore landed (SCORE 0, bay points 0), then the quit report with live values,
   leave it as it was.
3. A POINTS-BOUGHT BAY IS NOT RE-AWARDED: right after a ``'D'``-``'B'`` whose points the client then
   shows spent, the saved bay points are no more than the client's next report.

Each check must be able to fail: the break variants disable one guard and some run must then
disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import pytest

from fixtures.captures import capture_names, client_messages, load_capture
from fixtures.lobbies import city
from opencs import policy
from opencs.messages import DecodeError, client, decode_server, server
from opencs.session.persistence import Persistence
from opencs.store.base import ProgressRecord
from opencs.store.memory import MemoryProgressStore
from opencs.wire.frame import FrameReader
from opencs.world import player as player_module
from opencs.world.city import City, JoinResult
from opencs.world.progress import Progress, SavedProgress

KEY = "acct:REPLAY"
STALE = client.Status(0, 1, 0)  # what a client builds from its empty data image


class NoFloor(Progress):
    """Break variant: the SCORE floor does not exist."""

    def _below_floor(self, score: int) -> bool:
        return False


@dataclass
class Run:
    """One captured run: its client reports in order, and the record its session began from."""

    name: str
    statuses: list[client.Status] = field(default_factory=list)
    reports: list[client.Status | client.Bays] = field(default_factory=list)
    times: list[float] = field(default_factory=list)
    first_report: client.PodState | None = None
    seed: SavedProgress | None = None


def load(name: str) -> Run:
    run = Run(name)
    bays: server.SetBays | None = None
    status: server.SetStatus | None = None
    reader = FrameReader()
    for f in load_capture(name):
        if f.direction != "S":
            continue
        for frame in reader.feed(f.raw):
            try:
                msg, _ = decode_server(frame.payload)
            except DecodeError:
                continue
            if isinstance(msg, server.SetBays) and bays is None:
                bays = msg
            elif isinstance(msg, server.SetStatus) and status is None:
                status = msg
    if bays is not None:
        # What the client was restored to: bays and marker first, the bay points with them.
        run.seed = SavedProgress(bays.levels, bays.marker, status.bay_points if status else 0, 0, 0)
    for t, msg in client_messages(name):
        if isinstance(msg, client.PodState) and run.first_report is None:
            run.first_report = msg
            run.times.append(t)
        elif isinstance(msg, client.Status):
            run.statuses.append(msg)
            run.reports.append(msg)
            run.times.append(t)
        elif isinstance(msg, client.Bays):
            run.reports.append(msg)
            run.times.append(t)
    return run


def reporting_runs() -> list[Run]:
    """Runs whose client earned something: the corpus the three checks need."""
    runs = [load(n) for n in capture_names()]
    return [r for r in runs if r.first_report is not None and any(s.score for s in r.statuses)]


RUNS = reporting_runs()


class Session:
    """One session's city and persistence, over a store that outlives it."""

    def __init__(self, store: MemoryProgressStore, seed: SavedProgress | None) -> None:
        self.persistence = Persistence(store)
        self.city = City(city("A"))
        self.now = 0.0
        if seed is not None:
            store.save(KEY, ProgressRecord(*_fields(seed)))
        saved = self.persistence.load(KEY)
        joined = self.city.join("REPLAY", 0, 0.0, identity=KEY, saved=saved)
        assert isinstance(joined, JoinResult)
        self.pid = joined.player_id
        self.persistence.flush(self.city)

    def send(self, msg: client.ClientMessage, at: float) -> None:
        self.now = at
        self.city.client_message(self.pid, msg, at)
        self.persistence.flush(self.city)


def _fields(s: SavedProgress) -> tuple[tuple[int, ...], int, int, int, int]:
    return (s.bays, s.marker, s.bay_points, s.score, s.career)


@dataclass
class Outcome:
    run: str
    failures: list[str] = field(default_factory=list)


def check(run: Run) -> Outcome:
    out = Outcome(run.name)
    store = MemoryProgressStore()
    s = Session(store, run.seed)
    assert run.first_report is not None
    s.send(run.first_report, run.times[0])
    last_points: int | None = run.seed.bay_points if run.seed else None
    prev_bays = list(run.seed.bays) if run.seed else list(policy.NEW_PLAYER_BAYS)
    prev_marker = run.seed.marker if run.seed else policy.NEW_PLAYER_MARKER
    bought_last = False
    for i, msg in enumerate(run.reports):
        s.send(msg, run.times[i + 1])
        if isinstance(msg, client.Status):
            last_points = msg.bay_points
            bought_last = False
            continue
        later = next((m for m in run.reports[i + 1 :] if isinstance(m, client.Status)), None)
        saved = _saved(store).bay_points
        spent = later is not None and last_points is not None and later.bay_points < last_points
        raised = [n - o for o, n in zip(prev_bays, msg.levels, strict=True) if n != o]
        purchase = msg.marker == prev_marker and raised == [1]
        if spent and later is not None and saved > later.bay_points:  # check 3
            out.failures.append(
                f"3: bay bought with points: saved bay points {saved}, the client's next report "
                f"says {later.bay_points} -- a drop here re-awards the bay"
            )
        elif later is None and purchase and last_points and saved >= last_points:
            out.failures.append(
                f"3: bay bought with points just before the end: saved {saved} points with the "
                f"bay -- restoring them re-awards it"
            )
        bought_last = purchase and later is None
        prev_bays, prev_marker = list(msg.levels), msg.marker
    # The early captures' quit handshake: all zero, because those sessions had no state.
    s.send(STALE, run.times[-1] + 1.0)

    # 1. SAVED
    record = _saved(store)
    top = max(r.score for r in run.statuses)
    best = [r for r in run.statuses if r.score == top][-1]
    if record.score != top:
        out.failures.append(f"1: saved SCORE {record.score}, the highest reported was {top}")
    elif bought_last:
        if record.bay_points > best.bay_points:
            out.failures.append(f"1: saved bay points {record.bay_points} > report {best}")
    elif record.bay_points != best.bay_points:
        out.failures.append(f"1: saved bay points {record.bay_points}, expected {best.bay_points}")
    bays = [m for m in run.reports if isinstance(m, client.Bays)]
    if bays and record.bays != bays[-1].levels:
        out.failures.append(f"1: saved bays {record.bays}, last reported {bays[-1].levels}")

    # 2. SURVIVES A RECONNECT
    again = Session(store, None)
    again.send(run.first_report, 0.5)
    before = _saved(store)
    again.send(client.Status(0, 1, 0), 1.0)  # built before the restore landed
    held = _saved(store)  # a drop here must lose nothing
    again.send(client.Status(0, 1, before.bay_points), 2.0)  # the quit report, live values
    after = _saved(store)
    for when, record in (("after the stale report", held), ("after the quit report", after)):
        if replace(record, updated=0.0) != replace(before, updated=0.0):
            out.failures.append(
                f"2: the reconnect clobbered the record {when}: {before} -> {record}"
            )
    return out


def _saved(store: MemoryProgressStore) -> ProgressRecord:
    record = store.load(KEY)
    assert record is not None
    return record


def failures(*, broken: str | None, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    if broken == "floor":
        monkeypatch.setattr(player_module, "Progress", NoFloor)
    elif broken == "restore":
        monkeypatch.setattr(policy, "RESTORE_SETTLE", 0.0)
    elif broken == "points":
        monkeypatch.setattr(policy, "BAY_UPGRADE_POINTS", (0,) * 6)
    return {o.run: o.failures for o in map(check, RUNS) if o.failures}


def test_the_corpus_has_runs_with_scores_and_bay_purchases() -> None:
    names = {r.name for r in RUNS}
    for expected in (
        "2026-10-13-141108_007-resume-p1.txt",
        "2026-10-15-141418_003-resume-p1.txt",
    ):
        assert expected in names
    assert len(RUNS) >= 8
    assert sum(len(r.statuses) for r in RUNS) >= 40


@pytest.mark.parametrize("run", RUNS, ids=lambda r: r.name)
def test_the_save_path_keeps_what_every_run_reported(run: Run) -> None:
    assert check(run).failures == []


@pytest.mark.parametrize(
    ("broken", "check_number"),
    [("floor", "1"), ("restore", "2"), ("points", "3")],
)
def test_a_save_path_without_one_guard_fails_its_check(
    broken: str, check_number: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = failures(broken=broken, monkeypatch=monkeypatch)
    assert found, f"no run noticed the missing {broken} guard"
    assert any(f.startswith(check_number + ":") for fs in found.values() for f in fs)
