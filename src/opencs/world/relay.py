"""Client messages that are passed on to the other players (I §8.24, I §8.25, I §9.3.5).

Each function builds the server-to-client form of one client message; the city decides who gets it.
They are pure and they keep every coordinate as the client sent it (cell and signed offset), so an
off-map pod is never clamped (I §6.1).
"""

from __future__ import annotations

from opencs import policy
from opencs.messages import client, server
from opencs.wire.codec import BIAS88_MAX, BIAS88_MIN

#: The client multiplies a received forward speed by ``0xe666 / 0x10000`` (0.9, I §8.24), so the
#: relay divides by it first.
FWD_SCALE_NUM = 0xE666
FWD_SCALE_DEN = 0x10000


def relayed_speed(v: int) -> int:
    """A reported forward speed as relayed: ``ceil(v * 0x10000 / 0xe666)``, clamped to what a
    ``b88`` field may carry (I §8.24, I §4.3)."""
    w = -(-v * FWD_SCALE_DEN // FWD_SCALE_NUM)
    return max(BIAS88_MIN, min(BIAS88_MAX, w))


def pod_state(pod_id: int, report: client.PodState) -> server.PodState:
    """The ``'b'`` the other clients get for a pod's report (I §8.24).

    ``state`` is 1 if the reporter's is 1 and otherwise 0: 1 keeps ``v1``/``v2`` meaning vertical
    velocity, and 0 is the only value for which the client applies forward speed. Never 4 (the
    client discards the whole packet) or 2. ``v1``/``v2`` stay as sent, with the state they
    belong to (rule 1). ``fwd``/``fwd_target`` are divided by the client's 0.9 (``relayed_speed``)
    so the pod keeps its reported speed on other screens. The S→C form ends at the weight: no
    power, no armor.
    """
    state = 1 if report.state == policy.POD_STATE_FALLING else 0
    return server.PodState(
        id=pod_id,
        pos=report.pos,
        orient=report.orient,
        fwd=relayed_speed(report.fwd),
        fwd_target=relayed_speed(report.fwd_target),
        v1=report.v1,
        v2=report.v2,
        state=state,
        weight=report.weight,
    )


def grenade(thrown: client.Grenade) -> server.GrenadeInFlight:
    """The tagged ``'E'``-``'G'`` the other clients get for a thrown Grenade: the same fields
    (I §9.3.6, I §8). The thrower's own client already flies it."""
    return server.GrenadeInFlight(thrown.pos, thrown.yaw, thrown.pitch)


def shot(fired: client.Shot) -> server.Shot | None:
    """The ``'c'`` the other clients get for a shot, verbatim, or None if its KIND is one that
    crashes the client (I §8.25): the caller drops it and counts it. Only the reduced KINDs
    1, 2, 3 and 9 (and +10) are relayed."""
    if not fired.is_safe_kind:
        return None
    return server.Shot(fired.shooter, fired.pos, fired.orient, fired.pitch, fired.kind)
