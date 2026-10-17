"""Shots and hit reports as the city rules them (I §8.25, I §9.1, II §9.3, II §9, I §11).

A client's ``'c'`` is logged on its connection, checked for a shooter it may name and relayed; its
``'I'`` is a hit its simulation registered, credited by the shot log and then applied to whatever
was hit: a pod's damage is relayed to the victim, a tower's durability is reduced, an NPC's
likewise. Damage is an effect and credit is who is paid (II §9.1), so they
are decided apart. ``City`` owns one ``Hits`` and routes the two messages to it; it asks the city,
through ``WorldHost``, for the roster and for which entities exist.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from opencs.messages import WirePos, client
from opencs.world import relay
from opencs.world.attacks import in_fight_range
from opencs.world.combat import damage_to_victim
from opencs.world.effects import Effects
from opencs.world.events import Event, ToPlayer
from opencs.world.host import WorldHost
from opencs.world.orders import Orders
from opencs.world.player import Player
from opencs.world.pods import PodPhase

log = logging.getLogger("opencs.world")


class Damageable(Protocol):
    """An NPC population that takes hit reports (Sentinels, Munchers): ``hit`` returns None when
    the target is not one of its own, else the events the hit caused (usually none)."""

    def hit(self, target: int, damage_byte: int, now: float) -> list[Event] | None: ...


class Hits:
    """One city's shot relay and hit rules, with the counters of what it refused."""

    def __init__(
        self, host: WorldHost, orders: Orders, effects: Effects, npcs: Sequence[Damageable]
    ) -> None:
        self._host = host
        self._orders = orders
        self._effects = effects
        self._npcs = npcs
        #: Shots dropped because their KIND would crash a client (I §8.25).
        self.unsafe_shots = 0
        #: Shots whose shooter was neither the sender's pod nor a known NPC: a detection signal
        #: (I §11.4, II §9.2), counted and logged; the shot is still relayed.
        self.shooter_mismatches = 0
        #: Hit reports dropped, not relayed: a damage class the client cannot index (I §8.11), a
        #: report naming the reporter's own pod (I §9.1.4 corollary).
        self.unrelayable_damage = 0
        self.own_pod_hit_reports = 0

    def shot(self, player: Player, fired: client.Shot, now: float) -> list[Event]:
        """A shot (I §8.25, II §9.3, I §11, I App. B).

        It is logged on the sender's connection first, whatever the shooter and whether or not it
        is relayed: an NPC's shot the client simulated is what keeps its hit from being credited
        to the player (II §9.3). The shooter must be the sender's own pod or a known NPC;
        a mismatch is logged and the shot still relayed. A shot is relayed verbatim to every other
        client that has reported, except one whose KIND would crash a client, which is dropped and
        counted.
        """
        player.shots.record(now, fired.shooter)
        self.note_fire(fired.shooter, fired.pos, now, "ENEMY FIRE")
        if fired.shooter != player.id and not self._host.is_known_npc(fired.shooter):
            self.shooter_mismatches += 1
            # Warn once; a client that simulates NPCs this city does not know yet (before they
            # exist) would otherwise fill the log with one line per shot.
            level = logging.WARNING if self.shooter_mismatches == 1 else logging.DEBUG
            log.log(
                level,
                "player %d reported a shot by %d, which is neither its pod nor a known NPC",
                player.id,
                fired.shooter,
            )
        shot = relay.shot(fired)
        if shot is None:
            self.unsafe_shots += 1
            # Warn once, then DEBUG: a hostile client could otherwise log at the relay rate.
            level = logging.WARNING if self.unsafe_shots == 1 else logging.DEBUG
            log.log(level, "dropping a shot of KIND %d from player %d", fired.kind, player.id)
            return []
        return [Event(shot, self._host.playing(but=player.id))]

    def note_fire(self, shooter_id: int, origin: WirePos, now: float, why: str) -> None:
        """A shot or grenade left ``origin``: every pod that is not the shooter's own and not on its
        team, and stands within ``FIGHT_RANGE`` of it, is under attack (II §13 item 3, D4). A
        shooter that is not a player's pod is an NPC, everyone's enemy."""
        shooter = self._host.player(shooter_id)
        team = None if shooter is None else shooter.team
        for p in self._host.players():
            placed = p.pod.placed
            if (
                p.id == shooter_id
                or p.team == team
                or placed is None
                or p.pod.phase is PodPhase.DEAD
            ):
                continue
            if in_fight_range(placed.pos, origin):
                p.attacks.mark(now, why)

    def hit(self, reporter: Player, hit: client.HitReport, now: float) -> list[Event]:
        """An ``'I'``: a hit the reporter's simulation registered on ``hit.target`` (I §9.1).

        Damage is an effect and credit is who is paid (II §9.1), so they
        are decided apart. Credit: one shot of the reporter's is used up per report whatever the
        target (so an NPC-caused hit on a mine uses up the NPC's shot), and when that shot was the
        reporter's own pod's, the reporter joins the victim's hit set (II §9.3). Damage: a pod
        of another player gets the damage byte, whoever is credited and whatever the team (II §9.1).
        """
        if hit.target == reporter.id:
            # The client applies that damage itself and never reports it (I §9.1.4); relaying
            # one would apply it twice. None has been seen: a modified client.
            self.own_pod_hit_reports += 1
            level = logging.WARNING if self.own_pod_hit_reports == 1 else logging.DEBUG  # once
            log.log(level, "player %d reported a hit on its own pod; ignored", reporter.id)
            return []
        credit = reporter.shots.attribute(now)
        victim = self._host.player(hit.target)
        if victim is None:
            return self._hit_entity(reporter, hit, credit.credited, now)
        if victim.pod.phase is PodPhase.DEAD or not victim.pod.has_reported:
            log.debug("hit on player %d, which is not in play; ignored", victim.id)
            return []
        if credit.credited:
            victim.hits.record(reporter.id, now)
        if reporter.team != victim.team:
            victim.attacks.mark(now, "A HIT")  # II §13 item 3
        damage = damage_to_victim(reporter.id, hit.damage)
        if damage is None:
            self.unrelayable_damage += 1
            level = logging.WARNING if self.unrelayable_damage == 1 else logging.DEBUG  # once
            log.log(level, "dropping damage byte %d from player %d", hit.damage, reporter.id)
            return []
        victim.attacks.mark(now, "DAMAGE")
        return [Event(damage, ToPlayer(victim.id))]

    def _hit_entity(
        self, reporter: Player, hit: client.HitReport, credited: bool, now: float
    ) -> list[Event]:
        """A hit on something that is not a player's pod. A tower loses hit points (II §10); a mine
        is ignored; a Sentinel or Muncher loses hit points and is destroyed at 0 (II §16.2). The
        damage is the same whoever fired and whoever is credited."""
        tower = self._orders.towers.get(hit.target)
        if tower is not None:
            return self._orders.hit_tower(reporter, tower, hit, now)
        if hit.target in self._effects.mines:
            log.debug("player %d hit mine %d; mines have no durability", reporter.id, hit.target)
            return []  # mines have no durability (ours)
        for npcs in self._npcs:
            events = npcs.hit(hit.target, hit.damage, now)
            if events is not None:
                return events
        log.debug(
            "player %d hit entity %d (credited=%s); not a thing with durability",
            reporter.id,
            hit.target,
            credited,
        )
        return []
