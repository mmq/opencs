"""Two real sockets in one city: introductions, relay, chat, Grenade and leaving (I §7.2,
I §8.23, I §8.24, I §9.3.6, I App. A.2), over the whole server with no sleeping."""

from __future__ import annotations

import asyncio

from fixtures.loopback import TIMEOUT, Client, LogWatch, TickGate, connect, local_config, start
from fixtures.pods import report
from opencs.app import build_services
from opencs.clock import ManualClock
from opencs.messages import ServerMessage, WirePos, client, client_payload, server
from opencs.wire.frame import build_frame


def frame(msg: client.ClientMessage) -> bytes:
    return build_frame(client_payload(msg), terminator=b"\x0a")


async def enter(port: int, team: str) -> tuple[Client, server.Session]:
    c = await connect(port)
    await c.send("HHH\rJOINGENIE\r<Not Telling!>\rM 1380;3\r")
    await c.expect(b"\x05PRESS RETURN\r")
    await c.send("\r")
    await c.expect(b"\x05A\r")
    await c.send(f"/PLAY A {team}\r")
    await c.expect(b"\x05E 1 1\r")
    await c.send("/GO 0\r")
    await c.expect(b"\x05C\r")
    hello = await c.expect_frame("B")
    assert isinstance(hello, server.Session)
    return c, hello


def test_two_players_see_each_other_chat_throw_and_leave() -> None:
    async def scenario() -> None:
        # No ticker runs unless pulsed, so no repeated 'A' or 'B' mixes into what is expected.
        clock, gate = ManualClock(1000.0), TickGate()
        config = local_config()
        services = build_services(config, clock=clock)
        stop, task, port = await start(
            config, clock=clock, services=services, tick_sleep=gate.sleep
        )
        p1, h1 = await enter(port, "RED")
        assert await _drop(p1) == server.ReleaseDrop(h1.self_id)
        p2, h2 = await enter(port, "RED")
        assert (h1.self_id, h2.self_id) == (0, 1) and h2.team == h1.team

        # P2's first report introduces the pair, G then A each way, then the relayed 'b'.
        await p2.send(frame(report(4, x_off=75, z_off=75)))
        g = await p1.expect_frame("G")
        assert isinstance(g, server.Introduce) and (g.id, g.callsign) == (1, b"JOINGEN2")
        assert await p1.expect_frame("A") == server.ReleaseDrop(1)
        relayed = await p1.expect_frame("b")
        assert isinstance(relayed, server.PodState) and (relayed.id, relayed.state) == (1, 0)
        assert await p2.expect_frame("A") == server.ReleaseDrop(1)  # its own drop
        g = await p2.expect_frame("G")
        assert isinstance(g, server.Introduce) and g.id == 0
        assert await p2.expect_frame("A") == server.ReleaseDrop(0)

        # Open chat reaches the typist too; team chat reaches the team.
        await p1.send(frame(client.Chat(0, b"ok")))
        for c in (p1, p2):
            assert await c.expect_frame("a") == server.Chat(0, 0, b"ok")
        await p2.send(frame(client.Chat(1, b"team")))
        for c in (p1, p2):
            assert await c.expect_frame("a") == server.Chat(1, 1, b"team")

        # A Grenade from P1 reaches P2 only.
        thrown = client.Grenade(WirePos(2, 2, 100, 72, 300), 869, 3332)
        await p1.send(frame(thrown))
        assert await p2.expect_frame("E") == server.GrenadeInFlight(thrown.pos, 869, 3332)

        # P2 moves; P1 sees it. Then P2 quits and P1 is told with 'H'.
        await p2.send(frame(report(0, x_off=-40)))
        moved = await p1.expect_frame("b")
        assert isinstance(moved, server.PodState) and moved.pos.x_off == -40
        with LogWatch("opencs.session", "quit requested") as quit_seen:
            await p2.send(frame(client.Quit()))
            await quit_seen.reached(1)
        assert await p1.expect_frame("H") == server.Delete(1)

        for c in (p1, p2):
            c.writer.close()
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)

    asyncio.run(asyncio.wait_for(scenario(), 4 * TIMEOUT))


async def _drop(c: Client) -> ServerMessage:
    await c.send(frame(report(4)))
    return await c.expect_frame("A")
