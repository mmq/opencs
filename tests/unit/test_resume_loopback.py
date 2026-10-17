"""A line drop and /RESUME through real loopback sockets (M10b; II §13).

A scripted client plays, its socket goes away (closed, or silent for 15 s on the server's manual
clock), and a new connection of the same account is told the game is held, types /RESUME and is
put back in the same city and team with its SCORE, multiplier and bays. Nothing waits for time."""

from __future__ import annotations

import asyncio

from fixtures.loopback import (
    TIMEOUT,
    Client,
    LogWatch,
    TickGate,
    connect,
    frame,
    local_config,
    start,
)
from fixtures.pods import report
from opencs import policy
from opencs.clock import ManualClock
from opencs.messages import client, server

BAYS = (3, 2, 2, 1, 1, 0, 0, 0, 0)


async def login(port: int, account: str) -> Client:
    c = await connect(port)
    await c.send(f"HHH\r{account}\r<Not Telling!>\rM 1380;3\r")
    await c.expect(b"\x05PRESS RETURN\r")
    await c.send("\r")
    await c.expect(b"\x05A\r")
    await c.expect(b"TYPE /HELP FOR COMMANDS.\r\x05D0\r")
    return c


async def play_and_report(c: Client) -> server.Session:
    await c.send("/PLAY B\r")
    await c.expect(b"\x05E 2 2\r")
    await c.send("/GO 0\r")
    await c.expect(b"\x05C\r")
    hello = await c.expect_frame("B")
    assert isinstance(hello, server.Session)
    await c.send(frame(report(4)))
    await c.expect_frame("D")
    await c.expect_frame("D")
    await c.send(frame(report(0)))
    await c.send(frame(client.Bays(BAYS, 2)))
    await c.send(frame(client.Status(260, 3, 710)))
    return hello


async def resume(port: int, team: int) -> None:
    c = await login(port, "ALICE,secret")
    await c.expect(b"WAS INTERRUPTED.\r")
    await c.expect(b"TYPE /RESUME WITHIN 3:00 TO RETURN TO IT.\r\x05D0\r")
    await c.send("/RESUME\r")
    await c.expect(b"\x05E 2 2\r")
    await c.send("/GO 0\r")
    await c.expect(b"\x05C\r")
    hello = await c.expect_frame("B")
    assert isinstance(hello, server.Session) and hello.team == team
    await c.send(frame(report(4)))
    bays, status = await c.expect_frame("D"), await c.expect_frame("D")
    assert bays == server.SetBays(BAYS, 2)
    # The held SCORE and multiplier, not a new game's 0 and the city's entry floor.
    assert status == server.SetStatus(260, 3, 710)
    c.writer.close()


def test_a_closed_socket_is_a_line_drop_that_can_be_resumed() -> None:
    async def scenario() -> None:
        clock, gate = ManualClock(1000.0), TickGate()
        stop, task, port = await start(local_config(), clock=clock, tick_sleep=gate.sleep)
        try:
            with LogWatch("opencs.session", "held for /RESUME") as dropped:
                c = await login(port, "ALICE,secret")
                hello = await play_and_report(c)
                await c.send(frame(client.Status(260, 3, 710)))  # the last the server hears
                c.writer.close()
                await dropped.reached(1)
            clock.advance(60.0)
            await resume(port, hello.team)
        finally:
            stop.set()
            await asyncio.wait_for(task, TIMEOUT)

    asyncio.run(scenario())


def test_a_silent_line_is_a_line_drop_that_can_be_resumed() -> None:
    async def scenario() -> None:
        clock, gate = ManualClock(1000.0), TickGate()
        stop, task, port = await start(local_config(), clock=clock, tick_sleep=gate.sleep)
        try:
            with LogWatch("opencs.session", "held for /RESUME") as dropped:
                c = await login(port, "ALICE,secret")
                hello = await play_and_report(c)
                await c.send(frame(client.Status(260, 3, 710)))
                await c.send(frame(client.Ping()))  # answered only after the frames before it
                assert isinstance(await c.expect_frame("E"), server.PingReply)
                clock.advance(policy.SILENCE_SECONDS)
                gate.pulse()  # the session ticker notices
                await dropped.reached(1)
            await resume(port, hello.team)
        finally:
            stop.set()
            await asyncio.wait_for(task, TIMEOUT)

    asyncio.run(scenario())
