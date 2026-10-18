"""A client's ``'b'`` report: the pod's handshake and, on its first, the player going live.

Split from ``City`` because the first report carries the most (introductions, the territory, the
restored status) and ``/RESUME``'s restore lands here too. Functions of a city, not part of it: they
read what the city exposes and return events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opencs.messages import client, server
from opencs.world import relay
from opencs.world.dispatch import check_loadout, start_status
from opencs.world.entities import inform, introduce
from opencs.world.events import Event, ToHolders, ToPlayer
from opencs.world.player import Player
from opencs.world.pods import PodPhase

if TYPE_CHECKING:
    from opencs.world.city import City


def pod_state(city: City, player: Player, report: client.PodState, now: float) -> list[Event]:
    """A ``'b'``: the pod's own handshake, then, on its first, the introductions (I §7.2),
    then the relay to the clients that hold it (I §8.24)."""
    first = not player.pod.has_reported
    phase = player.pod.phase
    check_loadout(player, report)
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
        events += go_live(city, player, now)
        # A resumed game is not entered through the city's entry floor (II §13 item 5).
        floor = 1 if player.resume is not None else city.multiplier_floor
        events += start_status(player, floor, now)
    if player.modules_to_restore and player.pod.phase is PodPhase.IN_PLAY:
        events += restore_modules(player)  # the pod has landed (II §13)
    if player.pod.phase is not PodPhase.DEAD:  # a fly-over's 'b' is not the pod's place
        events.append(
            Event(
                relay.pod_state(player.id, report),
                ToHolders(player.id, except_player=player.id),
            )
        )
    return events


def restore_modules(player: Player) -> list[Event]:
    """A resumed pod has landed: one ``'Q'`` sub 5 per module of the held ledger, highest level
    first (II §13 item 5). Each goes in the ledger as an install pending the weight."""
    modules, player.modules_to_restore = player.modules_to_restore, []
    events = []
    for module in modules:
        player.loadout.install(module)
        events.append(Event(server.InstallModule(module), ToPlayer(player.id)))
    return events


def go_live(city: City, player: Player, now: float) -> list[Event]:
    """The player's first ``'b'`` is in: it is given the territory grid and every entity already
    live (pods, towers, guppies), and the pods are told about it, once per pair (I §7.2,
    I §7.3). Players that have not reported yet are introduced when they do."""
    events: list[Event] = []
    events += inform(city.territory, player.view, now)
    events += inform(city.weather, player.view, now)
    for entity in city.live.values():
        events += introduce(entity, player.view, now)
    city.live[player.id] = player
    for other in city.players():
        if other.pod.has_reported:
            events += introduce(player, other.view, now)
    return events
