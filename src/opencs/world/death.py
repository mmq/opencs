"""A pod's death: payout, kill notices, the ``'J'`` and the bounty zero (I §9.2.9, II §11.2).

On the client's bare ``'J'`` the steps are, in this order (steps 1 to 3 are the server's
bookkeeping; 4 and 5 are wire order and are not interchangeable):

1. Every non-teammate in the victim's 20 s hit set is paid the victim's entire bounty times its own
   multiplier. The client credits itself from its own copy, so this is the server's record only.
2. Each payee is sent ``'E'``-``'K'``, earned or not. Wire order is the spec's listing: these go
   before the ``'J'`` (I §9.2.9).
3. The victim's mirror takes the client's silent reset: multiplier 1, battle marker down one.
4. ``'J'`` goes to every client, the victim's too.
5. Only then is the bounty zeroed and pushed: the payees' clients read their copy of it when the
   ``'J'`` lands (observed: with the zero first, four paid kills printed ``0 x 1 = 0``). The
   victim's hit set is cleared.
"""

from __future__ import annotations

import logging

from opencs.messages import server
from opencs.world import scoring
from opencs.world.events import Event, ToPlayer
from opencs.world.host import WorldHost
from opencs.world.player import Player

log = logging.getLogger("opencs.world")


def settle(
    victim: Player,
    payees: list[Player],
    destroy: list[Event],
    now: float,
    *,
    as_of: float | None = None,
) -> list[Event]:
    """The events of a death. ``payees`` are the players to pay (the hit set within the window,
    not teammates); ``destroy`` is the ``'J'`` event(s) the victim's pod produced. ``as_of`` is
    when the death is valued, ``now`` unless the pod went quiet earlier (a silent line)."""
    bounty = victim.bounty.value(now if as_of is None else as_of)
    events: list[Event] = []
    for payee in payees:
        paid = scoring.payout(payee.id, bounty, payee.score.multiplier)
        earned = scoring.is_earned_kill(victim.score, payee.score)
        payee.score.server_credit += paid.points
        if earned:
            payee.score.marker = scoring.clamp_marker(
                payee.score.marker + 1
            )  # the client does this itself
        log.info(
            "player %d paid %d (bounty %d x %d) for the death of %d; %s kill",
            payee.id,
            paid.points,
            paid.bounty,
            paid.multiplier,
            victim.id,
            "earned" if earned else "not earned",
        )
        events.append(Event(server.KillNotice(earned), ToPlayer(payee.id)))
    victim.score.on_own_death(now)
    victim.grace.arm(now)  # the client's new-life reset re-arms it (I §9.1.5)
    victim.hits.clear()
    victim.attacks.new_life(now)  # the attacks that killed it are paid; a drop later is no fight
    return events + destroy + victim.bounty.zero()


def player_died(
    host: WorldHost, victim: Player, now: float, *, as_of: float | None = None
) -> list[Event]:
    """The victim's pod died (its bare ``'J'``, or a line drop in a fight, II §13 item 3): the
    pod's own steps, then ``settle``. A second death before the respawn changes nothing. A line
    that went quiet at ``as_of`` is paid and valued as of then: who hit it up to that moment, and
    the bounty it had (II §13 item 3, II §11.2), though it is noticed at ``now``."""
    destroy = victim.pod.on_death(now)
    if not destroy:
        return []
    victim.loadout.clear()  # the client empties every bay on its death (I §9.3.4, II §13)
    events = settle(
        victim, payees(host, victim, now if as_of is None else as_of), destroy, now, as_of=as_of
    )
    # The client lowered its marker silently, so the record takes the mirror's (II §11.3).
    victim.progress.on_own_death(victim.score.marker)
    return events


def payees(host: WorldHost, victim: Player, now: float) -> list[Player]:
    """The players to pay for a death: those in the victim's hit set within the window, still in
    the city, and not on the victim's team (I §9.2.8, II §11.2)."""
    found = (host.player(pid) for pid in victim.hits.payees(now))
    return [p for p in found if p is not None and p.team != victim.team]
