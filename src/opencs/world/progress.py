"""What of a player's progress is kept between sessions, and when it may be (II §11.3).

The client runs the progression itself and reports it; the server saves from those reports under
guards, because a report can be stale or can disagree with what a server action has just done.
This module is the rules and nothing else: it holds one player's *record* and says when that
record changed. It never writes it. The city collects the changes as ``ProgressChanged`` notes and
the session layer persists them (DESIGN §3, ADR 2: the world does no I/O).

The multiplier is not part of the record: the manual restarts it at 1 each game (II §11.3). SCORE is
per game (D2): ``score`` is the latest game's, and starting a game adds it to ``career`` and starts
the new one at 0.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from opencs import policy
from opencs.messages import limits
from opencs.world.scoring import clamp_marker


@dataclass(frozen=True, slots=True)
class SavedProgress:
    """One player's record: the bays, the battle marker, the bay points, the latest game's SCORE
    and the career total of the games before it."""

    bays: tuple[int, ...]
    marker: int
    bay_points: int
    score: int
    career: int


#: A player with no record (II §11.3).
NEW_PROGRESS = SavedProgress(policy.NEW_PLAYER_BAYS, policy.NEW_PLAYER_MARKER, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class ProgressChanged:
    """A note from the city: this identity's record is now ``record``. Last note wins."""

    identity: str
    record: SavedProgress


@dataclass(slots=True)
class _Guard:
    """The restore guard (II §11.3): what the restore sent, and when."""

    score: int
    bay_points: int
    since: float


class Progress:
    """One player's record and the rules for changing it from the client's reports."""

    def __init__(self, loaded: SavedProgress | None) -> None:
        """Start a game (II §11.3, D2): the last game's SCORE joins the career total and
        the new game's SCORE is 0. ``loaded`` is the stored record, None for a new identity."""
        base = loaded or NEW_PROGRESS
        self._record = replace(base, score=0, career=base.career + base.score)
        self._unsaved = loaded is not None and self._record != loaded
        self._guard: _Guard | None = None

    @property
    def record(self) -> SavedProgress:
        return self._record

    def take_unsaved(self) -> SavedProgress | None:
        """The record if it changed since the last call, else None."""
        if not self._unsaved:
            return None
        self._unsaved = False
        return self._record

    def snapshot(self) -> SavedProgress:
        """The record as it stands, for a save of everyone (M12)."""
        return self._record

    def restored(self, now: float) -> None:
        """The restore ``'D'``-``'S'`` has been sent: from here a report is held until it shows
        at least the SCORE and bay points the client was given (II §11.3)."""
        self._guard = _Guard(self._record.score, self._record.bay_points, now)

    # --- the client's reports ----------------------------------------------------------------

    def on_status(self, score: int, bay_points: int, now: float) -> None:
        """The client's ``'D'``-``'S'`` (the multiplier is not kept).

        1. Restore guard: while armed, held until a report shows at least the restored SCORE and
           bay points, or ``RESTORE_SETTLE`` has passed (a report built before the restore landed
           says SCORE 0, bay points 0).
        2. SCORE floor: a SCORE below the last one saved this game is refused (the same stale
           report, once the guard is off). A report with negative bay points is not a state the
           client can be restored to, and is refused too."""
        guard = self._guard
        if guard is not None:
            shows_restore = score >= guard.score and bay_points >= guard.bay_points
            if not shows_restore and now - guard.since < policy.RESTORE_SETTLE:
                return
            self._guard = None
        if self._below_floor(score) or bay_points < 0:
            return
        self._set(replace(self._record, score=score, bay_points=bay_points))

    def _below_floor(self, score: int) -> bool:
        """True if ``score`` is under the last SCORE saved this game (the SCORE floor)."""
        return score < self._record.score

    def on_bays(self, levels: tuple[int, ...], marker: int) -> None:
        """The client's ``'D'``-``'B'``: saved as reported, except that a points-driven upgrade
        also spends the points (II §11.3).

        When exactly one bay is one level up and the marker is as saved, the bay was bought with
        bay points; the client sends no ``'D'``-``'S'`` for it, so until its next one the record
        would hold the new bay *and* the unspent points, and a drop would re-award the bay on
        restore. The saved points become ``max(0, saved - threshold)``, the threshold taken at the
        bay's level before the upgrade. The floor is the common case (the last report is nearly
        always just under the threshold) and 0 is the only value that cannot re-award the bay."""
        if len(levels) != limits.BAY_COUNT or any(
            not 0 <= level <= policy.MAX_MODULE_LEVEL for level in levels
        ):
            return
        marker = clamp_marker(marker)
        old = self._record
        points = old.bay_points
        changed = [(b, a) for b, a in zip(old.bays, levels, strict=True) if a != b]
        if marker == old.marker and len(changed) == 1 and changed[0][1] - changed[0][0] == 1:
            points = max(0, points - policy.BAY_UPGRADE_POINTS[changed[0][0]])
            if self._guard is not None:
                # The client has spent them: do not hold its next report for points it lacks.
                self._guard.bay_points = min(self._guard.bay_points, points)
        self._set(replace(old, bays=levels, marker=marker, bay_points=points))

    def on_own_death(self, marker: int) -> None:
        """The pod died: the client lowered its battle marker without a word (II §11.3),
        so the record takes the mirrored marker."""
        self._set(replace(self._record, marker=clamp_marker(marker)))

    def _set(self, record: SavedProgress) -> None:
        if record != self._record:
            self._record = record
            self._unsaved = True
