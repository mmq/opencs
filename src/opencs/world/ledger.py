"""The records a city says have changed (II §11.3, DESIGN ADR 2).

The world cannot write to a store, so a city notes each player's changed record here and the session
layer takes the notes after every call into the city and persists them. One note per identity: the
newest wins.
"""

from __future__ import annotations

from collections.abc import Iterable

from opencs.world.player import Player
from opencs.world.progress import ProgressChanged


class ProgressLedger:
    """Notes of changed records, by identity, until they are taken."""

    def __init__(self) -> None:
        self._unsaved: dict[str, ProgressChanged] = {}

    def note(self, player: Player) -> None:
        """Remember the player's record if it changed since the last note."""
        record = player.progress.take_unsaved()
        if record is not None and player.identity is not None:
            self._unsaved[player.identity] = ProgressChanged(player.identity, record)

    def take(self) -> list[ProgressChanged]:
        """The notes so far, which are cleared."""
        changed, self._unsaved = list(self._unsaved.values()), {}
        return changed

    @staticmethod
    def snapshot(players: Iterable[Player]) -> list[ProgressChanged]:
        """Every kept player's record as it stands, whether it changed or not."""
        return [
            ProgressChanged(p.identity, p.progress.snapshot())
            for p in players
            if p.identity is not None
        ]
