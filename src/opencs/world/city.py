"""One city: the state and rules of one joinable game (I §2.2, I §7, II §17).

Pure rules: calls in, events out (DESIGN §3.2, ADR 2). A ``City`` never sends, awaits, reads a clock
or touches a socket; time arrives as a ``now`` argument and everything it says is returned as
``Event`` values for the router. It is changed only from the event loop (DESIGN §3.3).

A city's game never ends (S13): players come and go, and the world lives while anyone is in it. With
nobody left it is reset, so the next game starts fresh (``leave``); M10 keeps it alive while a
``/RESUME`` slot is held.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from opencs import policy
from opencs.config import CityConfig
from opencs.messages import ClientMessage, client, limits, server
from opencs.world import chat, death, relay
from opencs.world.combat import damage_to_victim
from opencs.world.effects import Effects
from opencs.world.entities import Entity, inform, introduce
from opencs.world.events import Event, ToHolders, ToPlayer, ToPlayers
from opencs.world.ids import IdRange, IdSpace
from opencs.world.modules import Drops, ModuleRules
from opencs.world.orders import Orders
from opencs.world.player import Player
from opencs.world.pods import PodPhase
from opencs.world.scoring import multiplier_floor
from opencs.world.territory import Territory
from opencs.world.view import ClientView
from opencs.world.weather import WEATHER_KEY, Weather

log = logging.getLogger("opencs.world")


@dataclass(frozen=True, slots=True)
class JoinResult:
    """A player joined. ``hello`` is the ``'B'`` to send it (I §7.1)."""

    player_id: int
    team: int
    index: int
    hello: server.Session


@dataclass(frozen=True, slots=True)
class JoinRefused:
    """A join that cannot be honored."""

    reason: str


class City:
    """The world of one city letter."""

    def __init__(self, config: CityConfig) -> None:
        self.config = config
        #: The block-class map every client is sent once, on its first report (I §7.3).
        self._territory = Territory(config.blocks)
        self._players: dict[int, Player] = {}
        self._ids = IdSpace()
        #: The entities every client that is playing should hold: the pods that have reported
        #: (I §7.2 step 1). A late joiner is synced from this (I §7.3).
        self._live: dict[int, Entity] = {}
        #: The city's weather condition, sent on a client's first report and on every change.
        self.weather = Weather()
        #: Tower orders, the team guppies and the standing towers (I §9.4).
        self.drops = Drops(self)
        self.orders = Orders(
            self,
            self._territory,
            ModuleRules(config.max_module_level, frozenset(config.banned_modules)),
            self.drops,
        )
        #: The effect modules (I §9.3.5, II §16.1).
        self.effects = Effects(self, self.orders, self.weather)
        #: Shots dropped because their KIND would crash a client (I §8.25).
        self.unsafe_shots = 0
        #: Shots whose shooter was neither the sender's pod nor a known NPC: a detection signal
        #: (I §11.4, II §9.2), counted and logged; the shot is still relayed.
        self.shooter_mismatches = 0
        #: Hit reports dropped, not relayed: a damage class the client cannot index (I §8.11), a
        #: report naming the reporter's own pod (I §9.1.4 corollary).
        self.unrelayable_damage = 0
        self.own_pod_hit_reports = 0
        self._handlers: dict[
            type[ClientMessage], Callable[[Player, ClientMessage, float], list[Event]]
        ] = {
            client.PodState: lambda p, m, now: self._pod_state(p, _as(client.PodState, m), now),
            client.Status: lambda p, m, now: self._status(p, _as(client.Status, m), now),
            client.Bays: lambda p, m, now: self._bays(p, _as(client.Bays, m)),
            client.Chat: lambda p, m, now: self._chat(p, _as(client.Chat, m)),
            client.Grenade: lambda p, m, now: [
                Event(relay.grenade(_as(client.Grenade, m)), self._playing(but=p.id))
            ],
            client.Shot: lambda p, m, now: self._shot(p, _as(client.Shot, m), now),
            client.HitReport: lambda p, m, now: self._hit(p, _as(client.HitReport, m), now),
            client.TowerOrder: lambda p, m, now: self.orders.tower_order(
                p, _as(client.TowerOrder, m), now
            ),
            client.ModuleOrder: lambda p, m, now: self.orders.module_order(
                p, _as(client.ModuleOrder, m), now
            ),
            client.LegArrived: lambda p, m, now: self.orders.leg_arrived(
                p, _as(client.LegArrived, m), now
            ),
            client.PickupRequest: lambda p, m, now: self.drops.pickup(
                p, _as(client.PickupRequest, m).object, now
            ),
            client.ModuleRested: lambda p, m, now: self._module_rested(
                p, _as(client.ModuleRested, m)
            ),
            client.EjectRequest: lambda p, m, now: self.drops.eject(
                p, _as(client.EjectRequest, m).module, now
            ),
            client.ModuleUsedUp: lambda p, m, now: self._module_used_up(
                p, _as(client.ModuleUsedUp, m)
            ),
            client.EffectRequest: lambda p, m, now: self.effects.request(
                p, _as(client.EffectRequest, m).effect, now
            ),
            client.AirSupport: lambda p, m, now: self.orders.air_support(
                p, _as(client.AirSupport, m), now
            ),
            client.DropMe: lambda p, m, now: p.pod.on_drop_request(now),
            client.Died: lambda p, m, now: self._died(p, now),
        }

    # --- who is here -------------------------------------------------------------------------

    def players(self) -> Sequence[Player]:
        """Everyone in the city, by id."""
        return sorted(self._players.values(), key=lambda p: p.id)

    def player(self, player_id: int) -> Player | None:
        return self._players.get(player_id)

    # --- joining and leaving -----------------------------------------------------------------

    def join(self, callsign: str, team: int, now: float) -> JoinResult | JoinRefused:
        """Seat a player: an id, the lowest free index in its team (I §6.3) and the ``'B'``."""
        if len(self._players) >= self.config.capacity:
            return JoinRefused(f"CITY {self.config.letter} IS FULL")
        taken = {p.index for p in self._players.values() if p.team == team}
        free = [i for i in range(policy.MAX_PLAYERS_PER_TEAM) if i not in taken]
        if not free:
            return JoinRefused(f"{policy.TEAM_NAMES[team]} IS FULL")
        player_id = self._ids.allocate(IdRange.PLAYER, self._earliest_start())
        if player_id is None:
            return JoinRefused("NO FREE PLAYER ID")
        index = free[0]
        cfg = self.config
        try:
            # Build the 'B' before seating anyone: if it cannot be built, nobody is seated.
            hello = server.Session(
                self_id=player_id,
                index=index,
                team=team,
                callsign=callsign.encode("ascii"),
                network=policy.NETWORK_NAME.encode("ascii"),
                city=cfg.name.encode("ascii"),
                max_mult=cfg.max_multiplier,
                tower_output=cfg.tower_output,
                max_module_level=cfg.max_module_level,
            )
        except Exception:
            self._ids.release(IdRange.PLAYER, player_id, now)
            raise
        player = Player(player_id, callsign, team, index, ClientView(player_id, now))
        player.view.hold(player_id)  # the client creates its own pod from 'B' (I §7.1)
        # A fresh client already shows its own default palette (I §8.20), which is a client fact,
        # not the city's starting condition: it is sent one only once the weather differs from it.
        player.view.tell(WEATHER_KEY, limits.CLIENT_DEFAULT_PALETTE)
        self._players[player_id] = player
        return JoinResult(player_id, team, player.index, hello)

    def leave(self, player_id: int, now: float) -> list[Event]:
        """A player left (quit or line drop): free its id and delete its pod on the clients that
        hold it, never ``'J'`` which would respawn it there (I §7.6)."""
        player = self._players.pop(player_id, None)
        if player is None:
            return []
        self._ids.release(IdRange.PLAYER, player_id, now)
        self._live.pop(player_id, None)
        # Name the holders now: forgetting the pod below would leave ``ToHolders`` nobody to reach
        # by the time the router resolves it. The leaver is out of the roster, so its own client is
        # not among them.
        events = []
        for other in self.players():
            if other.view.holds(player_id):
                other.view.forget(player_id)
                events.append(Event(server.Delete(player_id), ToPlayer(other.id)))
        if not self._players:
            self._reset()
        return events

    def _reset(self) -> None:
        """Nobody is here: the next game starts fresh (S13)."""
        self._ids = IdSpace()
        self._live.clear()
        self.orders.reset()
        self.drops.reset()
        self.weather.reset()
        self.effects.reset()

    def _earliest_start(self) -> float | None:
        """When the longest-connected client started: the id-reuse guard's input (I §6.2, II §8)."""
        return min((p.view.joined_at for p in self._players.values()), default=None)

    # --- the client's messages and time ------------------------------------------------------

    def client_message(self, player_id: int, msg: ClientMessage, now: float) -> list[Event]:
        """One decoded message from a player. Unknown players and messages not yet ruled are
        ignored."""
        player = self._players.get(player_id)
        if player is None:
            return []
        handler = self._handlers.get(type(msg))
        if handler is None:
            log.debug("ignoring %s from player %d", type(msg).__name__, player_id)
            return []
        return handler(player, msg, now)

    def note_shot(self, player_id: int, fired: client.Shot, now: float) -> None:
        """Log a shot the session declined to pass on (it was over the shot budget): the shot log
        must see every ``'c'`` even when it is not relayed (II §9.3). The log is
        capped (``SHOT_LOG_MAX``), so a flood cannot grow it."""
        player = self._players.get(player_id)
        if player is not None:
            player.shots.record(now, fired.shooter)

    def tick(self, now: float) -> list[Event]:
        """Timed rules: the drop's repeated ``'A'`` (I §7.1, II §7) and the bounty pushes (I
        §9.2.12, II §11.2)."""
        events: list[Event] = []
        for player in self.players():
            events += player.pod.tick(now)
            events += player.bounty.tick(now)
            player.grace.tick(now)
        return events

    def _pod_state(self, player: Player, report: client.PodState, now: float) -> list[Event]:
        """A ``'b'``: the pod's own handshake, then, on its first, the introductions (I §7.2),
        then the relay to the clients that hold it (I §8.24)."""
        first = not player.pod.has_reported
        phase = player.pod.phase
        self._check_loadout(player, report)
        events = player.pod.on_report(report, now)
        if first:
            player.grace.arm(now)  # a spawn (I §9.1.5)
        if player.pod.phase is not PodPhase.DEAD:  # a fly-over's 'b' is the camera, not the pod
            player.grace.observe(report.pos, now)
        if phase is not PodPhase.IN_PLAY and player.pod.phase is PodPhase.IN_PLAY:
            events += player.bounty.start(now)  # left drop state: accruing (I §9.2.12, II §11.2)
        if first:
            # Nobody else holds this pod yet, so its drop 'A' is for its owner alone; the others
            # are given theirs by the introduction, after their 'G'. ``ToHolders`` resolves after
            # this call, when the introductions below have made them holders, so it is named.
            events = [
                Event(e.message, ToPlayer(player.id))
                if isinstance(e.message, server.ReleaseDrop)
                else e
                for e in events
            ]
            events += self._go_live(player, now)
            events += self._start_status(player)
        if player.pod.phase is not PodPhase.DEAD:  # a fly-over's 'b' is not the pod's place
            events.append(
                Event(
                    relay.pod_state(player.id, report),
                    ToHolders(player.id, except_player=player.id),
                )
            )
        return events

    def _check_loadout(self, player: Player, report: client.PodState) -> None:
        """Cross-check the installed-module ledger with the weight this ``'b'`` reports (I §9.3.4,
        II §13). A disagreement that outlasts the grace is logged, once per episode, and never acted
        on."""
        mismatch = player.loadout.report(report.weight)
        if mismatch is not None:
            first = player.loadout.disagreeing == policy.LOADOUT_GRACE_REPORTS + 1
            level = logging.WARNING if first else logging.DEBUG
            log.log(level, "LOADOUT MISMATCH player %d: %s", player.id, mismatch)

    @property
    def multiplier_floor(self) -> int:
        """The multiplier a spawn is raised to in this city (II §12)."""
        return multiplier_floor(self.config.max_module_level, self.config.max_multiplier)

    def _start_status(self, player: Player) -> list[Event]:
        """A session's status: ``'D'``-``'B'`` first, then ``'D'``-``'S'``, to the player alone
        (I §9.2.11, II §11.3, I §7.8). Without ``'D'``-``'B'`` the client's multiplier and SCORE
        never run (I §9.2.3). It goes with the first ``'b'``, not with the ``'B'``: the client is
        then past its load (a still-loading client's ring is nearly full of ``'B'`` repeats, I §7.1,
        II §7), and it is when test servers sent it (I §7.1). ``player.score`` is where M10 puts a
        saved player's bays, marker and bay points."""
        return [
            Event(message, ToPlayer(player.id))
            for message in player.score.start_message(self.multiplier_floor)
        ]

    def _status(self, player: Player, report: client.Status, now: float) -> list[Event]:
        """The client's ``'D'``-``'S'``: mirrored, and answered with the multiplier floor when one
        is due after a death (II §12)."""
        pushes = player.score.on_status(
            report, now, self.multiplier_floor, self.config.max_multiplier
        )
        return [Event(m, ToPlayer(player.id)) for m in pushes]

    def _bays(self, player: Player, report: client.Bays) -> list[Event]:
        """The client's ``'D'``-``'B'``: its bays and battle marker, mirrored (II §11.1)."""
        if not player.score.on_bays(report):
            log.warning("player %d reported a bay level the client cannot hold", player.id)
        return []

    def spawn(self, entity: Entity, now: float) -> list[Event]:
        """A new entity (a pod, tower, drop, mine, Sentinel, guppy or Muncher): every client that
        is playing gets its creating packets through ``introduce``, the function a later joiner's
        sync uses, and the city remembers it for them (I §7.3)."""
        self._live[entity.entity_id] = entity
        events: list[Event] = []
        for player in self.players():
            if player.pod.has_reported:  # its client is past the 'B'
                events += introduce(entity, player.view, now)
        return events

    def allocate_structure_id(self) -> int | None:
        """A free id for a tower (II §8)."""
        return self._ids.allocate(IdRange.STRUCTURE, self._earliest_start())

    def release_structure_id(self, entity_id: int, now: float) -> None:
        self._ids.release(IdRange.STRUCTURE, entity_id, now)

    def refuse_order(self, player_id: int, message: ClientMessage) -> list[Event]:
        """A tower or module order the session did not pass on (over the order budget): see
        ``Orders.refuse_order``."""
        player = self._players.get(player_id)
        return [] if player is None else self.orders.refuse_order(player, message)

    def despawn(self, entity_id: int) -> None:
        """The entity is gone: later joiners are not given it. Clients are told by the caller."""
        self._live.pop(entity_id, None)
        for player in self._players.values():
            player.view.forget(entity_id)

    def _playing(self, *, team: int | None = None, but: int | None = None) -> ToPlayers:
        """The clients that have reported, for messages every client takes (chat, shots,
        Grenades; I App. B means every client in the game). One still loading has
        its receive ring nearly full from the ``'B'`` repeat (I §7.1, II §7), and has been
        introduced to nobody, so it would drop chat and shots anyway (our audience rule (clients
        that have reported); see I App. B). ``team`` narrows to one team, ``but`` leaves one player
        out."""
        return ToPlayers(
            frozenset(
                p.id
                for p in self._players.values()
                if p.pod.has_reported and p.id != but and (team is None or p.team == team)
            )
        )

    def _go_live(self, player: Player, now: float) -> list[Event]:
        """The player's first ``'b'`` is in: it is given the territory grid and every entity already
        live (pods, towers, guppies), and the pods are told about it, once per pair (I §7.2,
        I §7.3). Players that have not reported yet are introduced when they do."""
        events: list[Event] = []
        events += inform(self._territory, player.view, now)
        events += inform(self.weather, player.view, now)
        for entity in self._live.values():
            events += introduce(entity, player.view, now)
        self._live[player.id] = player
        for other in self.players():
            if other.pod.has_reported:
                events += introduce(player, other.view, now)
        return events

    def _module_rested(self, player: Player, rested: client.ModuleRested) -> list[Event]:
        self.drops.rested(player, rested.object, rested.pos)
        return []

    def _module_used_up(self, player: Player, used: client.ModuleUsedUp) -> list[Event]:
        self.drops.used_up(player, used.module)
        return []

    def _chat(self, player: Player, typed: client.Chat) -> list[Event]:
        """A line the player typed, echoed to its audience (I §8.23)."""
        event = chat.chat_line(
            player.id,
            typed,
            open_to=self._playing(),
            team_to=self._playing(team=player.team),
        )
        if event is None:
            log.debug("dropping chat of style %d from player %d", typed.style, player.id)
            return []
        return [event]

    def _shot(self, player: Player, fired: client.Shot, now: float) -> list[Event]:
        """A shot (I §8.25, II §9.3, I §11, I App. B).

        It is logged on the sender's connection first, whatever the shooter and whether or not it
        is relayed: an NPC's shot the client simulated is what keeps its hit from being credited
        to the player (II §9.3). The shooter must be the sender's own pod or a known NPC;
        a mismatch is logged and the shot still relayed. A shot is relayed verbatim to every other
        client that has reported, except one whose KIND would crash a client, which is dropped and
        counted.
        """
        player.shots.record(now, fired.shooter)
        if fired.shooter != player.id and not self._is_known_npc(fired.shooter):
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
        return [Event(shot, self._playing(but=player.id))]

    def _hit(self, reporter: Player, hit: client.HitReport, now: float) -> list[Event]:
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
        victim = self._players.get(hit.target)
        if victim is None:
            return self._hit_entity(reporter, hit, credit.credited, now)
        if victim.pod.phase is PodPhase.DEAD or not victim.pod.has_reported:
            log.debug("hit on player %d, which is not in play; ignored", victim.id)
            return []
        if credit.credited:
            victim.hits.record(reporter.id, now)
        damage = damage_to_victim(reporter.id, hit.damage)
        if damage is None:
            self.unrelayable_damage += 1
            level = logging.WARNING if self.unrelayable_damage == 1 else logging.DEBUG  # once
            log.log(level, "dropping damage byte %d from player %d", hit.damage, reporter.id)
            return []
        return [Event(damage, ToPlayer(victim.id))]

    def _hit_entity(
        self, reporter: Player, hit: client.HitReport, credited: bool, now: float
    ) -> list[Event]:
        """A hit on something that is not a player's pod. A tower loses hit points (II §10); a mine
        is ignored; a Sentinel or Muncher is M9's, and until then it is ignored too."""
        tower = self.orders.towers.get(hit.target)
        if tower is not None:
            return self.orders.hit_tower(reporter, tower, hit, now)
        if hit.target in self.effects.mines:
            log.debug("player %d hit mine %d; mines have no durability", reporter.id, hit.target)
            return []  # mines have no durability (ours)
        log.debug(
            "player %d hit entity %d (credited=%s); not ruled yet",
            reporter.id,
            hit.target,
            credited,
        )
        return []

    def _is_known_npc(self, entity_id: int) -> bool:
        """An entity the city has spawned that is not a player's pod (a Sentinel, say)."""
        return entity_id in self._live and entity_id not in self._players

    def _died(self, player: Player, now: float) -> list[Event]:
        """The client's bare ``'J'``: its pod died. The payout, the notices, the ``'J'`` and the
        bounty zero are ``death.settle``; a second ``'J'`` before the respawn changes nothing."""
        died = player.pod.on_death(now)
        if not died:
            return []
        player.loadout.clear()  # the client empties every bay on its death (I §9.3.4, II §13)
        return death.settle(player, self._payees(player, now), died, now)

    def _payees(self, victim: Player, now: float) -> list[Player]:
        """The players to pay for a death: those in the victim's hit set within the window, still
        in the city, and not on the victim's team (I §9.2.8, II §11.2)."""
        found = (self._players.get(pid) for pid in victim.hits.payees(now))
        return [p for p in found if p is not None and p.team != victim.team]


def _as[M: ClientMessage](kind: type[M], msg: ClientMessage) -> M:
    assert isinstance(msg, kind)
    return msg
