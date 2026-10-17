"""Client messages that are passed on to the other players (I §8.24, I §8.25, I §9.3.5).

Each function builds the server-to-client form of one client message; the city decides who gets it.
They are pure and they keep every coordinate as the client sent it (cell and signed offset), so an
off-map pod is never clamped (I §6.1).
"""

from __future__ import annotations

from opencs import policy
from opencs.messages import client, server


def pod_state(pod_id: int, report: client.PodState) -> server.PodState:
    """The ``'b'`` the other clients get for a pod's report (I §8.24).

    ``state`` is 1 if the reporter's is 1 and otherwise 0: 1 keeps ``v1``/``v2`` meaning vertical
    velocity, and 0 is the only value for which the client applies forward speed. Never 4 (the
    client discards the whole packet) or 2. ``v1``/``v2`` stay as sent, with the state they
    belong to (rule 1). The S→C form ends at the weight: no power, no armor.
    """
    state = 1 if report.state == policy.POD_STATE_FALLING else 0
    return server.PodState(
        id=pod_id,
        pos=report.pos,
        orient=report.orient,
        fwd=report.fwd,
        fwd_target=report.fwd_target,
        v1=report.v1,
        v2=report.v2,
        state=state,
        weight=report.weight,
    )
