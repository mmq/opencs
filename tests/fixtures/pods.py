"""Builders for client messages the world tests feed in."""

from __future__ import annotations

from opencs.messages import WirePos, client


def report(state: int, *, y: int = 240, power: int = 3000) -> client.PodState:
    """A client ``'b'`` with this state byte (A.3's first report is state 4 at cell (0,0))."""
    return client.PodState(
        pos=WirePos(0, 0, 145, y, 5),
        orient=450,
        fwd=0,
        fwd_target=0,
        v1=0,
        v2=0,
        state=state,
        power=power,
        weight=0,
        armor0=0,
        armor1=0,
    )
