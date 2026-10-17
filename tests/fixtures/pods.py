"""Builders for client messages the world tests feed in."""

from __future__ import annotations

from opencs.messages import WirePos, client


def report(state: int, **fields: int) -> client.PodState:
    """A client ``'b'`` with this state byte (A.3's first report is state 4 at cell (0,0)).

    Keyword overrides: ``cell_x``, ``cell_z``, ``x_off``, ``y``, ``z_off``, ``orient``, ``fwd``,
    ``fwd_target``, ``v1``, ``v2``, ``power``, ``weight``.
    """
    f = {
        "cell_x": 0,
        "cell_z": 0,
        "x_off": 145,
        "y": 240,
        "z_off": 5,
        "orient": 450,
        "fwd": 0,
        "fwd_target": 0,
        "v1": 0,
        "v2": 0,
        "power": 3000,
        "weight": 0,
    }
    unknown = fields.keys() - f.keys()
    assert not unknown, f"unknown report fields {unknown}"
    f.update(fields)
    return client.PodState(
        pos=WirePos(f["cell_x"], f["cell_z"], f["x_off"], f["y"], f["z_off"]),
        orient=f["orient"],
        fwd=f["fwd"],
        fwd_target=f["fwd_target"],
        v1=f["v1"],
        v2=f["v2"],
        state=state,
        power=f["power"],
        weight=f["weight"],
        armor0=0,
        armor1=0,
    )
