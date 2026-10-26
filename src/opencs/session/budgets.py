"""The rate limits of one connection, kept for its whole life (DESIGN §5).

A connection moves between phases (logon, Chamber, game) as often as its client likes: ``/QUIT``
then ``M 1380;3`` re-enters the Chamber, ``/PLAY`` starts another game. If each phase built its own
buckets, every switch would hand a flooder a full allowance, so they live here, made once per
session, and the phases spend from them. The numbers are in ``policy``.
"""

from __future__ import annotations

from opencs import policy
from opencs.messages import ClientMessage, client
from opencs.ratelimit import TokenBucket
from opencs.session.badinput import WindowedCounter


class Budgets:
    """Every bucket and counter a session's phases spend from."""

    def __init__(self, now: float) -> None:
        # The Chamber (policy.CHAMBER_*).
        self.chamber_lines = TokenBucket(
            policy.CHAMBER_BURST_LINES, policy.CHAMBER_LINES_PER_SECOND, now
        )
        self.chamber_drops = WindowedCounter(
            policy.CHAMBER_FLOOD_DROPS, policy.CHAMBER_FLOOD_WINDOW
        )
        #: Trips from the Chamber to the ``*`` prompt (``/QUIT``), each of which can come back.
        self.reentries = TokenBucket(*policy.CHAMBER_REENTRY_LIMIT, now)
        # In game: bad frames, every frame, and each relayed kind (policy.RELAY_LIMIT_*).
        self.bad_input = WindowedCounter(policy.MAX_BAD_INPUT, policy.BAD_INPUT_WINDOW)
        self.frames = TokenBucket(*policy.FRAME_LIMIT, now)
        self.relay: dict[type[ClientMessage], TokenBucket] = {
            client.PodState: TokenBucket(*policy.RELAY_LIMIT_POD_STATE, now),
            client.Shot: TokenBucket(*policy.RELAY_LIMIT_SHOT, now),
            client.HitReport: TokenBucket(*policy.RELAY_LIMIT_HIT, now),
            client.Chat: TokenBucket(*policy.RELAY_LIMIT_CHAT, now),
            client.Grenade: TokenBucket(*policy.RELAY_LIMIT_GRENADE, now),
            client.TowerOrder: TokenBucket(*policy.RELAY_LIMIT_ORDER, now),
            client.ModuleOrder: TokenBucket(*policy.RELAY_LIMIT_ORDER, now),
            client.EffectRequest: TokenBucket(*policy.RELAY_LIMIT_EFFECT, now),
            client.AirSupport: TokenBucket(*policy.RELAY_LIMIT_EFFECT, now),
            client.PickupRequest: TokenBucket(*policy.RELAY_LIMIT_MODULE_OPS, now),
            client.EjectRequest: TokenBucket(*policy.RELAY_LIMIT_MODULE_OPS, now),
        }
