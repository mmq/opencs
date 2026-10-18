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
import random
from collections.abc import Mapping, Sequence

from opencs.config import CityConfig
from opencs.messages import ClientMessage, client, server
from opencs.world import chat, death, relay
from opencs.world.dispatch import (
    Handler,
    module_handlers,
    score_handlers,
)
from opencs.world.effects import Effects
from opencs.world.entities import Entity, introduce
from opencs.world.events import Event, ToPlayer, ToPlayers
from opencs.world.hits import Hits
from opencs.world.ids import IdRange, IdSpace
from opencs.world.ledger import ProgressLedger
from opencs.world.lifecycle import Lifecycle
from opencs.world.modules import Drops, ModuleRules
from opencs.world.npcs.munchers import Munchers
from opencs.world.npcs.sentinels import Sentinels
from opencs.world.orders import Orders
from opencs.world.player import Player
from opencs.world.podreport import pod_state
from opencs.world.progress import ProgressChanged, SavedProgress
from opencs.world.resume import (
    Dropped,
    NoReservations,
    Reservations,
    ResumeRecord,
    drop_player,
)
from opencs.world.scoring import multiplier_floor
from opencs.world.seating import Applicant, JoinRefused, JoinResult, seat
from opencs.world.territory import Territory
from opencs.world.weather import Weather

log = logging.getLogger("opencs.world")


class City:
    """The world of one city letter."""

    def __init__(
        self,
        config: CityConfig,
        rng: random.Random | None = None,
        reservations: Reservations | None = None,
        id_ranges: Mapping[IdRange, tuple[int, int]] | None = None,
    ) -> None:
        self.config = config
        self._id_ranges = id_ranges
        #: The slots held for dropped players, which the city keeps room and a world for (II §13
        #: item 4).
        self._reservations = reservations or NoReservations()
        #: True from the first player until the world is reset; a held slot keeps it true.
        self._in_game = False
        #: Where the city's random choices come from (Sentinel spots, free Muncher targets), so a
        #: test can seed them.
        self._rng = rng or random.Random()
        #: The block-class map every client is sent once, on its first report (I §7.3).
        self._territory = Territory(config.blocks)
        self._players: dict[int, Player] = {}
        self._ids = IdSpace(id_ranges)
        #: Records that changed since ``take_progress``: the world cannot write them, so it says
        #: what to write (ADR 2) and the session layer persists it.
        self._ledger = ProgressLedger()
        #: The entities every client that is playing should hold: the pods that have reported
        #: (I §7.2 step 1). A late joiner is synced from this (I §7.3).
        self._live: dict[int, Entity] = {}
        #: The city's weather condition, sent on a client's first report and on every change.
        self.weather = Weather(config.ambient)
        #: Tower orders, the team guppies and the standing towers (I §9.4).
        rules = ModuleRules(config.max_module_level, frozenset(config.banned_modules))
        self.drops = Drops(self, rules)
        self.orders = Orders(self, self._territory, rules, self.drops)
        #: Resident and called Sentinels (II §16.3).
        self.sentinels = Sentinels(self, config.sentinels, config.sentinel_respawn, self._rng)
        #: Munchers, called and free (I §9.5.2, II §16.2, II §16.2).
        self.munchers = Munchers(
            self, self.orders, self._territory, config.muncher_every, self._rng
        )
        #: The effect modules (I §9.3.5, II §16.1).
        self.effects = Effects(
            self, self.orders, self.weather, rules, self.sentinels, self.munchers
        )
        #: Shots and hit reports (I §9.1.2, II §9.3).
        self.hits = Hits(self, self.orders, self.effects, [self.sentinels, self.munchers])
        self._lifecycle = Lifecycle(
            self.weather, self.sentinels, self.munchers, self.orders, self.drops, self.effects
        )
        self._handlers: dict[type[ClientMessage], Handler] = {
            **module_handlers(self.orders, self.drops, self.effects, self.munchers),
            client.PodState: lambda p, m, now: pod_state(self, p, _as(client.PodState, m), now),
            **score_handlers(self.config.max_module_level, self.config.max_multiplier),
            client.Chat: lambda p, m, now: self._chat(p, _as(client.Chat, m)),
            client.Grenade: lambda p, m, now: self._grenade(p, _as(client.Grenade, m), now),
            client.Shot: lambda p, m, now: self.hits.shot(p, _as(client.Shot, m), now),
            client.HitReport: lambda p, m, now: self.hits.hit(p, _as(client.HitReport, m), now),
            client.DropMe: lambda p, m, now: p.pod.on_drop_request(now),
            client.Died: lambda p, m, now: death.player_died(self, p, now),
        }

    # --- who is here -------------------------------------------------------------------------

    @property
    def in_game(self) -> bool:
        """True from the first player until the world is reset (S13): a held slot keeps it so."""
        return self._in_game

    def players(self) -> Sequence[Player]:
        """Everyone in the city, by id."""
        return sorted(self._players.values(), key=lambda p: p.id)

    @property
    def territory(self) -> Territory:
        """The block-class map a client is sent on its first report (I §7.3)."""
        return self._territory

    @property
    def live(self) -> dict[int, Entity]:
        """The entities every playing client should hold (I §7.2 step 1), by id. A late joiner
        is synced from it; the first-report path adds to it."""
        return self._live

    # The shot and hit counters live in ``Hits``; the city exposes them for the tests and the admin
    # channel (M12).
    @property
    def unsafe_shots(self) -> int:
        return self.hits.unsafe_shots

    @property
    def shooter_mismatches(self) -> int:
        return self.hits.shooter_mismatches

    @property
    def unrelayable_damage(self) -> int:
        return self.hits.unrelayable_damage

    @property
    def own_pod_hit_reports(self) -> int:
        return self.hits.own_pod_hit_reports

    def is_known_npc(self, entity_id: int) -> bool:
        """An entity the city has spawned that is not a player's pod (a Sentinel, say)."""
        return entity_id in self._live and entity_id not in self._players

    def player(self, player_id: int) -> Player | None:
        return self._players.get(player_id)

    # --- joining and leaving -----------------------------------------------------------------

    def join(
        self,
        callsign: str,
        team: int,
        now: float,
        *,
        identity: str | None = None,
        saved: SavedProgress | None = None,
        resume: ResumeRecord | None = None,
    ) -> JoinResult | JoinRefused:
        """Seat a player: an id, the lowest free index in its team (I §6.3) and the ``'B'``.

        ``identity`` is the key its record is kept under (None: nothing is kept) and ``saved`` the
        stored record it starts from (None: a new identity). The client is restored to it when it
        sends its first ``'b'`` (I §9.2.11, II §11.3); starting the game rolls the last SCORE into
        the career, which is a change to save (``take_progress``). ``resume`` is the held game being
        returned to (II §13 item 5): the same game, so SCORE, bay points and multiplier come
        back as they were. A new account has no stored row yet and resumes all the same; only a
        player nothing is kept for (``identity`` None: the record could not be read) starts anew."""
        held = [t for key, t in self._reservations.slots(self.config.letter) if key != identity]
        seated = seat(
            self.config,
            self._players,
            held,
            self._ids,
            self._earliest_start(),
            self._any_client_has_had,
            Applicant(callsign, team, identity, saved, resume),
            now,
        )
        if isinstance(seated, JoinRefused):
            return seated
        player, hello = seated
        if not self._in_game:
            self._in_game = True
            self._lifecycle.start(now)
        self._players[player.id] = player
        self._ledger.note(player)
        return JoinResult(player.id, team, player.index, hello)

    def leave(self, player_id: int, now: float, *, keep_alive: bool = False) -> list[Event]:
        """A player left (quit or line drop): free its id and delete its pod on the clients that
        hold it, never ``'J'`` which would respawn it there (I §7.6). The world is reset
        when nobody is left and no slot is held for a dropped player (``keep_alive`` says one is
        about to be, II §13 item 4)."""
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
        if not keep_alive:
            self._reset_if_abandoned()
        return events

    def line_drop(
        self,
        player_id: int,
        now: float,
        *,
        fight_at: float | None = None,
        hold: bool = False,
        keep_record: bool = True,
    ) -> Dropped:
        """The player's line dropped (II §13): a fight it was losing is a death, anything else
        only the delete. ``fight_at`` is when the line actually went (default ``now``); ``hold``
        says the player is to be held for ``/RESUME`` and ``keep_record`` that a record is made at
        all. See ``world.resume``."""
        player = self._players.get(player_id)
        if player is None:
            return Dropped([], None)
        at = now if fight_at is None else fight_at
        try:
            return drop_player(self, player, now, fight_at=at, hold=hold, keep_record=keep_record)
        finally:
            self._ledger.note(player)  # a death at the drop changed the marker

    def _reset_if_abandoned(self) -> None:
        """Reset the world when nobody is here and nothing is held for anyone (S13)."""
        if Lifecycle.abandoned(self._players, self._reservations.slots(self.config.letter)):
            self._in_game = False
            self._ids = IdSpace(self._id_ranges)
            self._live.clear()
            self._lifecycle.reset()

    def _any_client_has_had(self, entity_id: int) -> bool:
        """Whether a connected client's registry may hold a node under this id (I §6.2)."""
        return any(p.view.has_had(entity_id) for p in self._players.values())

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
        try:
            return handler(player, msg, now)
        finally:
            # Also when the handler raised: the record changes only through ``Progress``'s guarded
            # methods, each a whole valid state, so what it changed before failing is worth saving.
            self._ledger.note(player)

    def take_progress(self) -> list[ProgressChanged]:
        """The records that changed since the last call, newest per identity, for the session layer
        to persist (II §11.3). Cleared by the call."""
        return self._ledger.take()

    def all_progress(self) -> list[ProgressChanged]:
        """Every kept player's record as it stands, whether it changed or not: a graceful shutdown
        saves them all (M12)."""
        return ProgressLedger.snapshot(self.players())

    def note_shot(self, player_id: int, fired: client.Shot, now: float) -> None:
        """Log a shot the session declined to pass on (it was over the shot budget): the shot log
        must see every ``'c'`` even when it is not relayed (II §9.3). The log is
        capped (``SHOT_LOG_MAX``), so a flood cannot grow it."""
        player = self._players.get(player_id)
        if player is not None:
            player.shots.record(now, fired.shooter)
            self.hits.note_fire(fired.shooter, fired.pos, now, "ENEMY FIRE")

    def tick(self, now: float) -> list[Event]:
        """Timed rules: the weather program (I §8.20), Sentinel replacements, Muncher meals
        and free Munchers (II §16.2), and each pod's drop ``'A'`` repeat (I §7.1, II §7) and
        bounty pushes (I §9.2.9)."""
        if self._in_game:
            self._reset_if_abandoned()  # a held slot ran out
        events: list[Event] = []
        events += self._tick_weather(now)
        events += self.sentinels.tick(now)
        events += self.munchers.tick(now)
        for player in self.players():
            events += player.pod.tick(now)
            events += player.bounty.tick(now)
            player.grace.tick(now)
        return events

    def _tick_weather(self, now: float) -> list[Event]:
        """Each program step that is due is sent, as a transition, to the clients past their
        load; the others are given the condition by their first-report sync (I §7.3)."""
        events: list[Event] = []
        views = [p.view for p in self.players() if p.pod.has_reported]
        for before, after in self.weather.tick(now):
            log.info("weather program: %s -> %s", before, after)
            events += self.weather.announce(views, before, now)
        return events

    @property
    def multiplier_floor(self) -> int:
        """The multiplier a spawn is raised to in this city (II §12)."""
        return multiplier_floor(self.config.max_module_level, self.config.max_multiplier)

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
        return self._ids.allocate(
            IdRange.STRUCTURE, self._earliest_start(), self._any_client_has_had
        )

    def release_structure_id(self, entity_id: int, now: float) -> None:
        self._ids.release(IdRange.STRUCTURE, entity_id, now)

    def allocate_id(self, which: IdRange) -> int | None:
        """A free id in any range (II §8): an NPC's."""
        return self._ids.allocate(which, self._earliest_start(), self._any_client_has_had)

    def release_id(self, which: IdRange, entity_id: int, now: float) -> None:
        self._ids.release(which, entity_id, now)

    def refuse_order(self, player_id: int, message: ClientMessage) -> list[Event]:
        """A tower or module order the session did not pass on (over the order budget): see
        ``Orders.refuse_order``."""
        player = self._players.get(player_id)
        return [] if player is None else self.orders.refuse_order(player, message)

    def guppy_home(self, team: int, now: float) -> list[Event] | None:
        """An operator warps a team's guppy home (M12): see ``Orders.guppy_home``."""
        return self.orders.guppy_home(team, now)

    def despawn(self, entity_id: int) -> None:
        """The entity is gone: later joiners are not given it. Clients are told by the caller."""
        self._live.pop(entity_id, None)
        for player in self._players.values():
            player.view.forget(entity_id)

    def playing(self, *, team: int | None = None, but: int | None = None) -> ToPlayers:
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

    def _grenade(self, player: Player, thrown: client.Grenade, now: float) -> list[Event]:
        """A thrown Grenade is relayed to the other clients (I §9.3.6); it is also an attack on any
        enemy pod near it (II §13 item 3)."""
        self.hits.note_fire(player.id, thrown.pos, now, "AN ENEMY GRENADE")
        return [Event(relay.grenade(thrown), self.playing(but=player.id))]

    def _chat(self, player: Player, typed: client.Chat) -> list[Event]:
        """A line the player typed, echoed to its audience (I §8.23)."""
        event = chat.chat_line(
            player.id,
            typed,
            open_to=self.playing(),
            team_to=self.playing(team=player.team),
        )
        if event is None:
            log.debug("dropping chat of style %d from player %d", typed.style, player.id)
            return []
        return [event]


def _as[M: ClientMessage](kind: type[M], msg: ClientMessage) -> M:
    assert isinstance(msg, kind)
    return msg
