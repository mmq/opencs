"""Builders for the Muncher tests: a city with players at a point, towers, a call, arrivals and
what the clients were sent."""

from __future__ import annotations

from fixtures.cities import seat
from fixtures.delivery import deliveries, to
from fixtures.pods import report
from opencs import policy
from opencs.messages import ServerMessage, WirePos, WireXZ, client, server
from opencs.world.city import City

HERE = (1500, 1500)
FIRST = policy.MUNCHER_IDS[0]
ECHO = 4  # a damage byte of weapon class 4: 500


def xz(x: int, z: int) -> WireXZ:
    return WireXZ.from_world(x, z)


def world(count: int = 2, **config: object) -> tuple[City, list[int]]:
    """Players standing at HERE in a city that stocks every effect module."""
    c, ids = seat([0, 1, 2][:count], max_module_level=5, **config)
    pos = WirePos.from_world(HERE[0], 20, HERE[1])
    for pid in ids:
        c.client_message(
            pid,
            report(0, cell_x=pos.cell_x, cell_z=pos.cell_z, x_off=pos.x_off, z_off=pos.z_off),
            1.0,
        )
    return c, ids


def tower(c: City, x: int, z: int, team: int = 3, now: float = 2.0) -> int:
    out = deliveries(c, c.orders.place_tower(team, WirePos.from_world(x, 20, z), False, now))
    return next(m.id for _, m in out if isinstance(m, server.SpawnTower))


def call(c: City, pid: int, now: float = 10.0) -> list[tuple[int, ServerMessage]]:
    return deliveries(c, c.client_message(pid, client.EffectRequest("C"), now))


def arrive(c: City, pid: int, muncher: int, now: float) -> list[tuple[int, ServerMessage]]:
    return deliveries(c, c.client_message(pid, client.LegArrived(muncher), now))


def tick(c: City, now: float) -> list[tuple[int, ServerMessage]]:
    """What a tick says about Munchers and towers (not the pods' repeated bounty pushes)."""
    keep = (server.MobileCreate, server.SendMuncher, server.Destroy)
    return [(p, m) for p, m in deliveries(c, c.tick(now)) if isinstance(m, keep)]


def ks(out: list[tuple[int, ServerMessage]], pid: int) -> list[WireXZ]:
    return [m.pos for m in to(out, pid) if isinstance(m, server.MobileCreate)]


def latches(out: list[tuple[int, ServerMessage]], pid: int) -> list[server.SendMuncher]:
    return [m for m in to(out, pid) if isinstance(m, server.SendMuncher)]


def destroys(out: list[tuple[int, ServerMessage]], pid: int) -> list[int]:
    return [m.id for m in to(out, pid) if isinstance(m, server.Destroy)]


def hit(c: City, pid: int, target: int, now: float = 20.0) -> list[tuple[int, ServerMessage]]:
    return deliveries(c, c.client_message(pid, client.HitReport(target, ECHO), now))


def join_late(c: City, now: float, team: int = 3) -> tuple[int, list[ServerMessage]]:
    """A client joins and sends its first ``'b'``; its id and everything it was then sent."""
    r = c.join(f"LATE{now:g}"[:8].upper().replace(".", "X"), team, now)
    assert hasattr(r, "player_id")
    pid: int = r.player_id  # type: ignore[union-attr]
    return pid, to(deliveries(c, c.client_message(pid, report(4), now + 0.5)), pid)
