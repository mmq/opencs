"""A line drop and the record it leaves for ``/RESUME`` (II §13 items 1-3).

A drop that is not "in the middle of a fight you are about to lose" only deletes the pod on the
other screens and leaves a record the player can come back to. One that is counts as the death it
was running from: the payouts and ``'E'``-``'K'`` to the hitters, ``'J'`` to every other client, the
bounty zero, the battle marker down by one and the modules gone, then the delete. The record is
data; holding it, the windows and the slot reservation are the session layer's, and this module
never reads a clock (the times come in).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from opencs import policy
from opencs.messages.catalog import module_level
from opencs.world import death
from opencs.world.attacks import in_fight_range
from opencs.world.events import Event, ToHolders, ToPlayer
from opencs.world.orders import Strike
from opencs.world.player import Player
from opencs.world.pods import PodPhase

if TYPE_CHECKING:
    from opencs.world.city import City


@dataclass(frozen=True, slots=True)
class ResumeRecord:
    """What a line drop leaves (II §13 item 2). ``multiplier`` is the last report's less the
    tower spends since; ``bay_points`` is the saved value (the points-upgrade guard may have spent
    it since the last report, II §11.3); ``modules`` is the confirmed ledger, highest
    level first. A record that is not ``eligible`` explains a refusal: ``reason`` says why."""

    identity: str
    callsign: str
    city: str
    team: int
    score: int
    multiplier: int
    bay_points: int
    modules: tuple[int, ...]
    dropped_at: float
    eligible: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Dropped:
    """A line drop's outcome: the events to deliver (in order) and the record, None when nothing
    is kept for the player (no identity, or not in the city)."""

    events: list[Event]
    record: ResumeRecord | None


class Reservations(Protocol):
    """The slots held for dropped players (II §13 item 4), asked of the session layer."""

    def slots(self, letter: str) -> Sequence[tuple[str, int]]:
        """``(identity key, team)`` of each slot held in the city with this letter."""
        ...


class NoReservations:
    """Nothing is ever held (a city built without a session layer)."""

    def slots(self, letter: str) -> Sequence[tuple[str, int]]:
        return ()


def battle_reason(player: Player, strikes: Iterable[Strike], fight_at: float) -> str:
    """Why a drop is "in the middle of a fight you are about to lose", or an empty string: the pod
    was under attack within ``BATTLE_WINDOW`` before ``fight_at``, the last moment the player was
    there (II §13 item 3, D4). What happened after it, to a pod frozen on other screens, is
    not the player's fight, and nor is what came before the pod's last death. A pod that never
    reported has no place to be attacked at, and one already dead has had its death."""
    if not player.pod.has_reported or player.pod.phase is PodPhase.DEAD:
        return ""
    since = fight_at - policy.BATTLE_WINDOW
    why = player.attacks.between(since, fight_at)
    if why:
        return why
    placed = player.pod.placed
    if placed is None:
        return ""
    since = max(since, player.attacks.life_started or since)
    for strike in strikes:
        if (
            since <= strike.at <= fight_at
            and strike.team != player.team
            and in_fight_range(placed.pos, strike.pos)
        ):
            return "AN ENEMY AIR STRIKE"
    return ""


def modules_highest_first(modules: Iterable[int]) -> tuple[int, ...]:
    """The restore order: the client puts each module in the best-fitting free bay, so the biggest
    go first and cannot be crowded out by small ones (I §9.3.4, II §13). Ties keep the ledger's
    order."""
    return tuple(sorted(modules, key=lambda m: -module_level(m)))


def drop_player(city: City, player: Player, now: float, *, fight_at: float, hold: bool) -> Dropped:
    """The player's line dropped. ``fight_at`` is when it did (``now``, or the last byte for a
    silent line); a fight is an attack within ``BATTLE_WINDOW`` before it. ``hold`` says the
    player will be held for a ``/RESUME``, which keeps the city alive when it empties."""
    reason = battle_reason(player, city.orders.strikes, fight_at)
    events: list[Event] = []
    if reason:
        died = death.player_died(city, player, now, as_of=fight_at)
        events = _name_holders(city, died, player.id)
    record = None
    if player.identity is not None:
        record = ResumeRecord(
            identity=player.identity,
            callsign=player.callsign,
            city=city.config.letter,
            team=player.team,
            score=player.progress.record.score,
            multiplier=player.score.live_multiplier,
            bay_points=player.progress.record.bay_points,
            modules=modules_highest_first(player.loadout.for_resume),
            dropped_at=now,
            eligible=not reason,
            reason=reason,
        )
    events += city.leave(player.id, now, keep_alive=hold and not reason)
    return Dropped(events, record)


def _name_holders(city: City, events: list[Event], entity_id: int) -> list[Event]:
    """Name the holders of ``entity_id`` as they are now: the pod is deleted from their views in
    the same call, and ``ToHolders`` resolves after it (I App. B, the M5 convention)."""
    named: list[Event] = []
    for event in events:
        audience = event.audience
        if isinstance(audience, ToHolders) and audience.entity_id == entity_id:
            named += [
                Event(event.message, ToPlayer(p.id))
                for p in city.players()
                if p.view.holds(entity_id) and p.id not in (entity_id, audience.except_player)
            ]
        else:
            named.append(event)
    return named
