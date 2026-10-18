"""Bounty, the client's score mirror and the death payout (I §9.2, II §11.1, II §11.1).

The client runs most of the manual's scoring for its own player (I §9.2.1): it steps the
multiplier, accrues SCORE and bay points, upgrades bays and counts its battle marker, and reports
all of it with ``'D'``-``'S'`` and ``'D'``-``'B'``. The server therefore keeps a *mirror* of each
client and pays with the client's value (I §10: "pay with the client's value"); it runs no
progression schedule of its own. What only the server can do is the economy between players: the
bounty a pod carries, who is paid when it dies, and whether a kill was earned.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from opencs import policy
from opencs.messages import client, server
from opencs.wire.codec import BIAS88_MAX, BIAS88_MIN
from opencs.world.events import Event, ToHolders


def multiplier_floor(max_module_level: int, max_multiplier: int) -> int:
    """The multiplier a spawn is raised to: the city's maximum module level, at least 1 and at
    most the city's maximum multiplier (II §12, II §11.3)."""
    return min(max(max_module_level, 1), max_multiplier)


def clamp_marker(marker: int) -> int:
    """The battle marker held within ``'D'``-``'B'``'s b88 field, which is what the client can
    hold, store or be sent (I §4.3, I §8). The mirror counts deaths and earned kills without
    bound, so every place it is stored or sent clamps it."""
    return max(BIAS88_MIN, min(BIAS88_MAX, marker))


class Bounty:
    """One pod's bounty: one wire unit per second while it lives (I §8.7).

    It starts when the pod leaves drop state and stops at its death. The clients count 10 a tick of
    their own between our pushes (I §9.2.12, II §11.2), so the push is a correction and the cadence
    is ours (``BOUNTY_PUSH_INTERVAL``).
    """

    def __init__(self, pod_id: int) -> None:
        self.pod_id = pod_id
        self._since: float | None = None
        self._next_push = 0.0

    @property
    def running(self) -> bool:
        return self._since is not None

    def start(self, now: float) -> list[Event]:
        """The pod is alive and out of drop state: accrue from here, and push 0 so the holders
        drop what their own accrual added while it was dead or dropping (I §9.2.9). Idempotent: a
        second call pushes nothing."""
        if self._since is not None:
            return []
        self._since = now
        self._next_push = now + policy.BOUNTY_PUSH_INTERVAL
        return [self._push(0)]

    def value(self, now: float) -> int:
        """The bounty in wire units (seconds of accrual) at ``now``; 0 when not running."""
        if self._since is None:
            return 0
        return int((now - self._since) * policy.BOUNTY_UNITS_PER_SECOND)

    def tick(self, now: float) -> list[Event]:
        """The ``'E'``-``'B'`` push to the clients that hold the pod, when one is due."""
        if self._since is None or now < self._next_push:
            return []
        self._next_push = now + policy.BOUNTY_PUSH_INTERVAL
        return [self._push(self.value(now))]

    def zero(self) -> list[Event]:
        """The pod died: stop accruing and push 0. Sent after the ``'J'`` (I §9.2.9 step 3),
        because nothing on the client clears the field."""
        self._since = None
        return [self._push(0)]

    def _push(self, value: int) -> Event:
        return Event(server.SetBounty(self.pod_id, value), ToHolders(self.pod_id))


@dataclass(slots=True)
class PlayerScore:
    """The server's mirror of one client's progression (II §11.1, I §10).

    Everything here is what the client last said (or what we last told it); none of it is trusted
    for anything durable (III §4; persistence is M10's).
    """

    score: int = 0
    multiplier: int = 1
    bay_points: int = 0
    bays: tuple[int, ...] = policy.NEW_PLAYER_BAYS
    marker: int = policy.NEW_PLAYER_MARKER
    #: What the server computed it paid this player for kills: informational. The client credits
    #: itself, and its own figure, readable from its next ``'D'``-``'S'``, is the truth (I §9.2.9
    #: step 2).
    server_credit: int = 0
    #: Multiplier points the client has spent on tower orders since its last ``'D'``-``'S'``, net of
    #: refunds (I §9.4.2: a tower costs 1, a MegaTower 2, every ``'O'`` refusal refunds 1). A
    #: ``'D'``-``'S'`` carries the live value, so this is what the mirror has not yet seen.
    tower_spent: int = 0
    #: When the pod last died and the multiplier floor has not yet been applied (II §12).
    floor_pending_since: float | None = field(default=None, repr=False)

    @property
    def highest_bay(self) -> int:
        """The highest bay level of the nine (I §9.2.9 step 1)."""
        return max(self.bays)

    @property
    def lowest_bay(self) -> int:
        """The lowest bay level of the nine. An empty bay (level 0, ``X``) counts as the lowest and
        fills first (I §9.2.7), so it is 0 here."""
        return min(self.bays)

    @property
    def live_multiplier(self) -> int:
        """The multiplier the client most likely has now: the last report less what tower orders
        have spent since (II §13 item 2). At least 1: the client's own floor."""
        return max(1, self.multiplier - max(0, self.tower_spent))

    def spend(self, points: int) -> None:
        """The client sent a tower order and spent ``points`` of its multiplier (I §9.4.2)."""
        self.tower_spent += points

    def refund(self) -> None:
        """The server refused a tower order: the client gives one point back (I §9.4.2)."""
        self.tower_spent -= 1

    def start_message(self, floor: int) -> list[server.SetBays | server.SetStatus]:
        """What a session starts with: ``'D'``-``'B'`` first, then ``'D'``-``'S'`` (II §11.3, I
        §7.8). Without the first the client's multiplier and SCORE never run (II §11.1). Sets the
        mirror to match: the multiplier is at least the floor and the marker is clamped to what
        ``'D'``-``'B'`` can carry."""
        self.multiplier = max(self.multiplier, floor)
        self.marker = clamp_marker(self.marker)
        return [
            server.SetBays(self.bays, self.marker),
            server.SetStatus(self.score, self.multiplier, self.bay_points),
        ]

    def on_status(
        self, report: client.Status, now: float, floor: int, max_multiplier: int
    ) -> list[server.SetStatus]:
        """The client's ``'D'``-``'S'``: mirror it. The first one at least ``FLOOR_SETTLE`` after a
        death, if its multiplier is below the floor, is answered with a ``'D'``-``'S'`` carrying
        that report's own SCORE and bay points and the floor multiplier, once per death (II §12). An
        earlier one may predate our ``'J'`` reaching the client, so its multiplier is not taken (the
        death set the mirror to the client's reset, 1)."""
        self.score, self.bay_points = report.score, report.bay_points
        self.tower_spent = 0  # the report carries the live multiplier
        died = self.floor_pending_since
        if died is not None and now - died < policy.FLOOR_SETTLE:
            return []
        self.multiplier = min(report.multiplier, max_multiplier)
        if died is None:
            return []
        self.floor_pending_since = None
        if self.multiplier >= floor:
            return []
        self.multiplier = floor
        return [server.SetStatus(report.score, floor, report.bay_points)]

    def on_bays(self, report: client.Bays) -> bool:
        """The client's ``'D'``-``'B'``: mirror the bays and the battle marker. False (nothing kept)
        for a level the client's table cannot hold."""
        if any(not 0 <= level <= policy.MAX_MODULE_LEVEL for level in report.levels):
            return False
        self.bays, self.marker = report.levels, report.marker
        return True

    def on_own_death(self, now: float) -> None:
        """The client's reset at its own pod's death, which it does silently (I §9.2.9): the
        multiplier back to 1 and the battle marker down by one. The floor is due once the client
        reports again."""
        self.multiplier = 1
        self.tower_spent = 0
        self.marker = clamp_marker(self.marker - 1)
        self.floor_pending_since = now


def is_earned_kill(victim: PlayerScore, payee: PlayerScore) -> bool:
    """I §9.2.9 step 1: ``victim.highest_bay >= payee.lowest_bay - 1``, from the mirrors."""
    return victim.highest_bay >= payee.lowest_bay - 1


@dataclass(frozen=True, slots=True)
class Payout:
    """What the server records as paid to one payee: the victim's entire bounty (in points, the
    wire value times 10) times the payee's own multiplier. The client credits itself; this is the
    server's record, and the client's figure is the truth (I §9.2.9 step 2)."""

    payee: int
    bounty: int
    multiplier: int

    @property
    def points(self) -> int:
        return self.bounty * self.multiplier


def payout(payee: int, bounty_units: int, multiplier: int) -> Payout:
    """The entire bounty times this payee's multiplier; not divided between payees (II §11.2)."""
    return Payout(payee, bounty_units * policy.BOUNTY_DISPLAY_FACTOR, multiplier)
