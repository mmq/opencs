"""Which world rule answers each order, drop, effect and score message (I §9.4, I §9.3.5, II §16.1,
I §9.2).

``City`` owns the rule objects and a table from client message type to handler. The messages about
orders, dropped modules, effect modules and the score protocol need nothing of the city but those
objects, so their entries are built here and ``City`` only merges them into its table (DESIGN §8:
one concept per module, split a file that grows).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from opencs import policy
from opencs.messages import ClientMessage, client
from opencs.world.effects import Effects
from opencs.world.events import Event, ToPlayer
from opencs.world.modules import Drops
from opencs.world.npcs.munchers import Munchers
from opencs.world.orders import Orders
from opencs.world.player import Player
from opencs.world.scoring import multiplier_floor

log = logging.getLogger("opencs.world")

type Handler = Callable[[Player, ClientMessage, float], list[Event]]


def _as[M: ClientMessage](kind: type[M], msg: ClientMessage) -> M:
    assert isinstance(msg, kind)
    return msg


def module_handlers(
    orders: Orders, drops: Drops, effects: Effects, munchers: Munchers
) -> dict[type[ClientMessage], Handler]:
    """The entries for tower and module orders, guppy and Muncher legs, pickups, rests, ejects,
    used-up reports, effect requests and Air Support."""
    return {
        client.TowerOrder: lambda p, m, now: orders.tower_order(p, _as(client.TowerOrder, m), now),
        client.ModuleOrder: lambda p, m, now: orders.module_order(
            p, _as(client.ModuleOrder, m), now
        ),
        client.LegArrived: lambda p, m, now: _leg_arrived(
            orders, munchers, p, _as(client.LegArrived, m), now
        ),
        client.PickupRequest: lambda p, m, now: drops.pickup(
            p, _as(client.PickupRequest, m).object, now
        ),
        client.ModuleRested: lambda p, m, now: _rested(drops, p, _as(client.ModuleRested, m)),
        client.EjectRequest: lambda p, m, now: drops.eject(
            p, _as(client.EjectRequest, m).module, now
        ),
        client.ModuleUsedUp: lambda p, m, now: _used_up(drops, p, _as(client.ModuleUsedUp, m)),
        client.EffectRequest: lambda p, m, now: effects.request(
            p, _as(client.EffectRequest, m).effect, now
        ),
        client.AirSupport: lambda p, m, now: orders.air_support(p, _as(client.AirSupport, m), now),
    }


def _leg_arrived(
    orders: Orders, munchers: Munchers, player: Player, arrival: client.LegArrived, now: float
) -> list[Event]:
    """A leg report is a guppy's or a Muncher's: the id says which (I §6.2, I §9.4.1, I §9.5.2)."""
    if munchers.owns(arrival.id):
        return munchers.arrived(player, arrival.id, now)
    return orders.leg_arrived(player, arrival, now)


def _rested(drops: Drops, player: Player, rested: client.ModuleRested) -> list[Event]:
    drops.rested(player, rested.object, rested.pos)
    return []


def _used_up(drops: Drops, player: Player, used: client.ModuleUsedUp) -> list[Event]:
    drops.used_up(player, used.module)
    return []


def check_loadout(player: Player, report: client.PodState) -> None:
    """Cross-check the installed-module ledger with the weight this ``'b'`` reports (I §9.3.4, II
    §13). A disagreement that outlasts the grace is logged, once per episode, and never acted on."""
    mismatch = player.loadout.report(report.weight)
    if mismatch is not None:
        first = player.loadout.disagreeing == policy.LOADOUT_GRACE_REPORTS + 1
        level = logging.WARNING if first else logging.DEBUG
        log.log(level, "LOADOUT MISMATCH player %d: %s", player.id, mismatch)


def score_handlers(
    max_module_level: int, max_multiplier: int
) -> dict[type[ClientMessage], Handler]:
    """The entries for the client's ``'D'``-``'S'`` and ``'D'``-``'B'`` (I §9.2.5, II §12)."""
    floor = multiplier_floor(max_module_level, max_multiplier)

    def status(player: Player, msg: ClientMessage, now: float) -> list[Event]:
        """The client's ``'D'``-``'S'``: mirrored, and answered with the multiplier floor when one
        is due after a death (II §12)."""
        report = _as(client.Status, msg)
        player.progress.on_status(report.score, report.bay_points, now)
        pushes = player.score.on_status(report, now, floor, max_multiplier)
        return [Event(m, ToPlayer(player.id)) for m in pushes]

    def bays(player: Player, msg: ClientMessage, now: float) -> list[Event]:
        """The client's ``'D'``-``'B'``: its bays and battle marker, mirrored (II §11.1)."""
        report = _as(client.Bays, msg)
        if player.score.on_bays(report):
            player.progress.on_bays(report.levels, report.marker)
        else:
            log.warning("player %d reported a bay level the client cannot hold", player.id)
        return []

    return {client.Status: status, client.Bays: bays}


def start_status(player: Player, floor: int, now: float) -> list[Event]:
    """A session's status: ``'D'``-``'B'`` first, then ``'D'``-``'S'``, to the player alone (I
    §9.2.11, II §11.3, I §7.8). Without ``'D'``-``'B'`` the client's multiplier and SCORE never run
    (I §9.2.3). It goes with the first ``'b'``, not with the ``'B'``: the client is then past its
    load (a still-loading client's ring is nearly full of ``'B'`` repeats, I §7.1, II §7), and it is
    when test servers sent it (I §7.1). ``player.score`` holds the restored player's bays, marker
    and bay points (``Player`` builds it from the stored record), and the restore guard starts here
    (II §11.3)."""
    player.progress.restored(now)
    return [Event(message, ToPlayer(player.id)) for message in player.score.start_message(floor)]
