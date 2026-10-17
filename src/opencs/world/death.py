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
from opencs.world.player import Player

log = logging.getLogger("opencs.world")


def settle(victim: Player, payees: list[Player], destroy: list[Event], now: float) -> list[Event]:
    """The events of a death. ``payees`` are the players to pay (the hit set within the window,
    not teammates); ``destroy`` is the ``'J'`` event(s) the victim's pod produced."""
    bounty = victim.bounty.value(now)
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
    return events + destroy + victim.bounty.zero()
