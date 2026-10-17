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
from dataclasses import dataclass, field

from opencs import policy
from opencs.config import CityConfig
from opencs.messages import ClientMessage, ServerMessage, client, server
from opencs.world import chat, relay
from opencs.world.entities import Entity, introduce
from opencs.world.events import Event, ToHolders, ToPlayer
from opencs.world.ids import IdRange, IdSpace
from opencs.world.pods import Pod, PodPhase
from opencs.world.view import ClientView

log = logging.getLogger("opencs.world")


@dataclass(slots=True)
class Player:
    """A player in a city: identity, slot, what its client holds and its pod's life."""

    id: int
    callsign: str
    team: int
    index: int  # 0-3 within the team; unique per (team, index), I §6.3
    view: ClientView
    pod: Pod = field(init=False)

    def __post_init__(self) -> None:
        self.pod = Pod(self.id)

    @property
    def entity_id(self) -> int:
        return self.id

    def creating_messages(self) -> Sequence[ServerMessage]:
        """``'G'`` at the pod's last position, then ``'A'``: ``'G'`` leaves the new pod in drop
        state and a pod in drop state discards every ``'b'`` (I §7.2 steps 2 and 3). A pod that
        is dead and awaiting its drop is introduced where it last stood; the normal ``'A'`` flow
        then applies (I §7.2)."""
        placed = self.pod.placed
        assert placed is not None, "a pod is introduced only after its first 'b' (I §7.2)"
        return (
            server.Introduce(
                id=self.id,
                callsign=self.callsign.encode("ascii"),
                index=self.index,
                team=self.team,
                pos=placed.pos,
                orient=placed.orient,
            ),
            server.ReleaseDrop(self.id),
        )


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
        self._players: dict[int, Player] = {}
        self._ids = IdSpace()
        #: The entities every client that is playing should hold: the pods that have reported
        #: (I §7.2 step 1). A late joiner is synced from this (I §7.3).
        self._live: dict[int, Entity] = {}
        self._handlers: dict[
            type[ClientMessage], Callable[[Player, ClientMessage, float], list[Event]]
        ] = {
            client.PodState: lambda p, m, now: self._pod_state(p, _as(client.PodState, m), now),
            client.Chat: lambda p, m, now: self._chat(p, _as(client.Chat, m)),
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

    def tick(self, now: float) -> list[Event]:
        """Timed rules: the drop's repeated ``'A'`` (I §7.1, II §7)."""
        events: list[Event] = []
        for player in self.players():
            events += player.pod.tick(now)
        return events

    def _pod_state(self, player: Player, report: client.PodState, now: float) -> list[Event]:
        """A ``'b'``: the pod's own handshake, then, on its first, the introductions (I §7.2),
        then the relay to the clients that hold it (I §8.24)."""
        first = player.pod.last is None
        events = player.pod.on_report(report, now)
        if first:
            # Nobody else holds this pod yet, so its drop 'A' is for its owner alone; the others
            # are given theirs by the introduction, after their 'G'.
            events = [Event(e.message, ToPlayer(player.id)) for e in events]
            events += self._go_live(player)
        if player.pod.phase is not PodPhase.DEAD:  # a fly-over's 'b' is not the pod's place
            events.append(
                Event(
                    relay.pod_state(player.id, report),
                    ToHolders(player.id, except_player=player.id),
                )
            )
        return events

    def _go_live(self, player: Player) -> list[Event]:
        """The player's first ``'b'`` is in: it is told about every pod already live, and they are
        told about it, once per pair (I §7.2). Players that have not reported yet are
        introduced when they do."""
        events: list[Event] = []
        for entity in self._live.values():
            events += introduce(entity, player.view)
        self._live[player.id] = player
        for other in self.players():
            if other.id in self._live:
                events += introduce(player, other.view)
        return events

    def _chat(self, player: Player, typed: client.Chat) -> list[Event]:
        """A line the player typed, echoed to its audience (I §8.23)."""
        event = chat.chat_line(player.id, player.team, typed)
        if event is None:
            log.debug("dropping chat of style %d from player %d", typed.style, player.id)
            return []
        return [event]

    def _died(self, player: Player, now: float) -> list[Event]:
        """The seam M6 extends with payout and bounty before the ``'J'`` (I §9.2.9, II §11.2)."""
        return player.pod.on_death(now)


def _as[M: ClientMessage](kind: type[M], msg: ClientMessage) -> M:
    assert isinstance(msg, kind)
    return msg
