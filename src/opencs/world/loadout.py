"""The installed-module ledger of one pod (I §9.3.4, II §13).

The client persists nothing and a ``/RESUME`` must restore modules, so the server keeps what each
pod carries. It can, exactly, from messages it handles: installs happen only through ``'Q'`` sub 0
(a pickup grant) and sub 5 (M10's resume); every removal is reported with its module (eject = sub 1;
used up, destroyed, shield beaten down = sub 4); death clears all. The one thing the client reports
about its loadout, its weight in every ``'b'`` (the sum of the modules' weights, plus 100 while a
High Jump or Performance module is active), cross-checks the ledger.

An install can fail silently (no free bay of a sufficient level), so an install is *pending* until a
report's weight shows it, and is dropped as never landed once ``LOADOUT_GRACE_REPORTS`` further
reports match without it. A disagreement that outlasts the same grace is a mismatch: counted and
logged, never acted on. Pure: it takes the facts and answers.
"""

from __future__ import annotations

from opencs import policy
from opencs.messages.catalog import (
    ACTIVE_MODULE_WEIGHT,
    CATALOG,
    WEIGHT_FLAG_MODULES,
)


def weight_of(modules: list[int]) -> int:
    """The sum of the catalog weights (unknown indices weigh nothing)."""
    return sum(CATALOG[m].weight for m in modules if m in CATALOG)


class Loadout:
    """What one pod carries, as far as the server's own messages say."""

    def __init__(self) -> None:
        #: Confirmed installs: a multiset, two of one module is legal.
        self.modules: list[int] = []
        #: Installs sent but not yet seen in a reported weight, oldest first.
        self.pending: list[int] = []
        #: Installs the weight showed never landed.
        self.failed: list[int] = []
        #: Reports past the grace window that disagreed, in all.
        self.mismatches = 0
        #: Consecutive reports that disagree since the last change or match.
        self.disagreeing = 0
        self._pending_age = 0

    @property
    def carried(self) -> list[int]:
        """Every module the ledger holds, confirmed or pending: what a resume would restore."""
        return [*self.modules, *self.pending]

    def install(self, module: int) -> None:
        """The server granted a pickup (or sent ``'Q'`` sub 5) of this module."""
        self.pending.append(module)
        self.disagreeing = 0
        self._pending_age = 0

    def remove(self, module: int) -> bool:
        """The client reported ``'Q'`` sub 1 or sub 4 for this module. False if the ledger did not
        hold it. A pending copy is the last resort: a confirmed one is the one that went."""
        self.disagreeing = 0
        for bag in (self.modules, self.pending):
            if module in bag:
                bag.remove(module)
                return True
        return False

    def clear(self) -> None:
        """The pod died: the client empties every bay."""
        self.modules.clear()
        self.pending.clear()
        self.disagreeing = 0
        self._pending_age = 0

    def report(self, weight: int) -> str | None:
        """The weight in one ``'b'``. Pending installs are settled oldest first: confirmed once the
        weight includes them, dropped as never landed after the grace. Returns a description when
        the ledger and the client disagree past the grace, else None."""
        for k in range(len(self.pending), -1, -1):
            held = [*self.modules, *self.pending[:k]]
            expected = weight_of(held)
            flagged = any(m in WEIGHT_FLAG_MODULES for m in held)
            if weight == expected or (flagged and weight == expected + ACTIVE_MODULE_WEIGHT):
                self._settle(k)
                return None
        self.disagreeing += 1
        if self.disagreeing <= policy.LOADOUT_GRACE_REPORTS:
            return None
        self.mismatches += 1
        return (
            f"client weight {weight}, ledger {weight_of(self.modules)} "
            f"{sorted(self.modules)} + pending {self.pending}"
        )

    def _settle(self, confirmed: int) -> None:
        """A report matched with the oldest ``confirmed`` pending installs landed."""
        self.modules.extend(self.pending[:confirmed])
        self.pending = self.pending[confirmed:]
        self.disagreeing = 0
        if not self.pending:
            self._pending_age = 0
            return
        self._pending_age += 1
        if self._pending_age > policy.LOADOUT_GRACE_REPORTS:
            self.failed.extend(self.pending)
            self.pending = []
            self._pending_age = 0
