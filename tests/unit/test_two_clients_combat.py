"""Two real sockets, two teams: a fight with damage both ways, a death, its payout and notice, the
respawn and the multiplier floor (I §9.1, I §9.2, I §9.2.5, I §9.2.9, II §11.2, II §9, II §12), over
the whole server with no sleeping. The ManualClock moves only after the server has answered what
came before."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fixtures.loopback import TIMEOUT, Client, TickGate, enter, frame, local_config, start
from fixtures.pods import report
from opencs.app import build_services
from opencs.clock import ManualClock
from opencs.messages import client, server
from opencs.world.scoring import multiplier_floor


@dataclass
class Fight:
    clock: ManualClock
    gate: TickGate
    red: Client
    blue: Client
    red_id: int
    blue_id: int
    floor: int


async def join_both(port: int, clock: ManualClock, gate: TickGate) -> Fight:
    """Both players are in, introduced, out of drop state, and have their session-start status."""
    red, h1 = await enter(port, "RED")
    await red.send(frame(report(4)))
    assert await red.expect_frame("A") == server.ReleaseDrop(h1.self_id)
    floor = multiplier_floor(h1.max_module_level, h1.max_mult)
    # The session starts with the bays, then the status (I §9.2.11, II §11.3, I §7.8).
    assert await red.expect_frame("D") == server.SetBays((1, 1, 1, 1, 0, 0, 0, 0, 0), 0)
    assert await red.expect_frame("D") == server.SetStatus(0, floor, 0)
    blue, h2 = await enter(port, "BLUE")
    await blue.send(frame(report(4)))
    await blue.expect_frame("D")  # bays
    assert await blue.expect_frame("D") == server.SetStatus(0, floor, 0)
    await red.expect_frame("b")  # blue's introduction is complete
    await red.send(frame(report(0)))
    await blue.expect_frame("b")
    await blue.send(frame(report(0)))
    await red.expect_frame("b")  # both are out of drop state at t=1000
    return Fight(clock, gate, red, blue, h1.self_id, h2.self_id, floor)


async def exchange_fire(f: Fight) -> None:
    """Red shoots and, a second later, reports the hit: blue gets the shot and the damage. Then
    blue hits red back: friendly or not, damage is relayed."""
    await f.red.send(frame(client.Shot(f.red_id, report(0).pos, 3196, 0, 1)))
    relayed = await f.blue.expect_frame("c")
    assert isinstance(relayed, server.Shot) and relayed.id == f.red_id
    f.clock.advance(1.0)
    await f.red.send(frame(client.HitReport(f.blue_id, 20)))
    assert await f.blue.expect_frame("I") == server.Damage(f.red_id, 20)
    await f.blue.send(frame(client.Shot(f.blue_id, report(0).pos, 3196, 0, 1)))
    await f.red.expect_frame("c")
    f.clock.advance(1.0)
    await f.blue.send(frame(client.HitReport(f.red_id, 3)))
    assert await f.red.expect_frame("I") == server.Damage(f.blue_id, 3)


async def blue_dies_and_respawns(f: Fight) -> None:
    # Ten seconds on, both bounties are pushed to both clients.
    f.clock.advance(10.0)
    f.gate.pulse()
    for c in (f.red, f.blue):
        pushes = [await c.expect_frame("E") for _ in range(2)]
        assert {m.id for m in pushes if isinstance(m, server.SetBounty)} == {f.red_id, f.blue_id}
    # Blue dies. Red, who hit it, is told the kill was earned, then 'J' reaches both, then the
    # bounty is zeroed (I §9.2.9).
    await f.blue.send(frame(client.Died()))
    assert await f.red.expect_frame("E") == server.KillNotice(True)
    assert await f.red.expect_frame("J") == server.Destroy(f.blue_id)
    assert await f.red.expect_frame("E") == server.SetBounty(f.blue_id, 0)
    assert await f.blue.expect_frame("J") == server.Destroy(f.blue_id)
    assert await f.blue.expect_frame("E") == server.SetBounty(f.blue_id, 0)
    # Blue respawns; its first status two seconds on is raised to the floor, with its own SCORE.
    await f.blue.send(frame(client.DropMe()))
    assert await f.blue.expect_frame("A") == server.ReleaseDrop(f.blue_id)
    f.clock.advance(3.0)
    await f.blue.send(frame(client.Status(500, 1, 20)))
    assert await f.blue.expect_frame("D") == server.SetStatus(500, f.floor, 20)


def test_a_cross_team_fight_ends_in_a_paid_death_and_a_floored_respawn() -> None:
    async def scenario() -> None:
        clock, gate = ManualClock(1000.0), TickGate()
        config = local_config()
        services = build_services(config, clock=clock)
        stop, task, port = await start(
            config, clock=clock, services=services, tick_sleep=gate.sleep
        )
        fight = await join_both(port, clock, gate)
        await exchange_fire(fight)
        await blue_dies_and_respawns(fight)
        for c in (fight.red, fight.blue):
            c.writer.close()
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)

    asyncio.run(asyncio.wait_for(scenario(), 4 * TIMEOUT))
