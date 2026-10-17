"""Progress survives a server restart (M10a; II §11.3).

Two complete servers run one after the other on the same ``data_dir``, each a real listener on a
loopback socket with the SQLite store the app opens itself. A scripted client plays the first,
reports its progress and quits with Shift-Q; the second is a new app that has never seen the
client, and must restore what it reported. Nothing waits for time."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

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
from opencs.config import AppConfig
from opencs.messages import client, server
from opencs.world.scoring import multiplier_floor

type Scenario = Callable[[int, ManualClock, TickGate], Awaitable[None]]

BAYS = (3, 2, 2, 1, 1, 0, 0, 0, 0)
MARKER = 2


def services_floor(config: AppConfig) -> int:
    """City B's multiplier floor: its maximum module level capped at its maximum multiplier."""
    b = next(c for c in config.cities if c.letter == "B")
    return multiplier_floor(b.max_module_level, b.max_multiplier)


def config_in(data_dir: Path) -> AppConfig:
    config = local_config()
    return replace(config, server=replace(config.server, data_dir=data_dir))


async def login(port: int, account: str) -> Client:
    c = await connect(port)
    await c.send(f"HHH\r{account}\r<Not Telling!>\rM 1380;3\r")
    await c.expect(b"\x05PRESS RETURN\r")
    await c.send("\r")
    await c.expect(b"\x05A\r")
    await c.expect(b"TYPE /HELP FOR COMMANDS.\r\x05D0\r")  # the welcome, so replies line up
    return c


async def ask(c: Client, line: str) -> bytes:
    """A Chamber command and its whole reply (the reply ends with its color reset)."""
    await c.send(f"{line}\r")
    return await c.expect(b"\x05D0\r")


async def play(c: Client) -> server.Session:
    """/PLAY B into the game and the first ``'b'``; returns the ``'B'``."""
    await c.send("/PLAY B\r")
    await c.expect(b"\x05E 2 2\r")
    await c.send("/GO 0\r")
    await c.expect(b"\x05C\r")
    hello = await c.expect_frame("B")
    assert isinstance(hello, server.Session)
    await c.send(frame(report(4)))
    return hello


async def restored(c: Client) -> tuple[server.SetBays, server.SetStatus]:
    """The ``'D'``-``'B'`` and then the ``'D'``-``'S'`` a first report is answered with."""
    bays, status = await c.expect_frame("D"), await c.expect_frame("D")
    assert isinstance(bays, server.SetBays) and isinstance(status, server.SetStatus)
    return bays, status


async def run_server(data_dir: Path, session: Scenario) -> None:
    """Start an app on ``data_dir``, run ``session(port)`` against it, then stop it and wait for
    it to finish closing its database."""
    clock, gate = ManualClock(1000.0), TickGate()
    stop, task, port = await start(
        config_in(data_dir), clock=clock, on_disk=True, tick_sleep=gate.sleep
    )
    try:
        await session(port, clock, gate)
    finally:
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)


def test_bays_and_bay_points_survive_a_server_restart(tmp_path: Path) -> None:
    async def first(port: int, clock: ManualClock, gate: TickGate) -> None:
        c = await login(port, "ALICE,secret")
        assert b"NO RECORD YET" in await ask(c, "/SCORE")
        await play(c)
        bays, status = await restored(c)
        assert bays == server.SetBays(policy.NEW_PLAYER_BAYS, policy.NEW_PLAYER_MARKER)
        assert (status.score, status.bay_points) == (0, 0)  # a new account starts from defaults
        # The client's own progression, reported as it happens, then Shift-Q's handshake.
        await c.send(frame(client.Bays(BAYS, MARKER)))
        await c.send(frame(client.Status(250, 2, 700)))
        await c.send(frame(client.Status(260, 2, 710)))
        await c.send(frame(client.Quit()))
        assert await c.expect_frame("C") == server.QuitAck()
        c.writer.close()

    async def second(port: int, clock: ManualClock, gate: TickGate) -> None:
        c = await login(port, "ALICE,secret")  # the password survived too
        record = await ask(c, "/SCORE")
        for line in (
            b"LAST GAME SCORE: 260",
            b"BAY POINTS: 710",
            b"BAYS: 3 2 2 1 1 0 0 0 0",
            b"BATTLE MARKER: 2",
        ):
            assert line in record
        hello = await play(c)
        assert isinstance(hello, server.Session)
        bays, status = await restored(c)
        assert bays == server.SetBays(BAYS, MARKER)  # 'D'-'B' carries the saved bays
        floor = services_floor(config_in(tmp_path))
        assert (status.score, status.multiplier, status.bay_points) == (0, floor, 710)
        c.writer.close()

    async def scenario() -> None:
        with LogWatch("opencs.session", "logged out") as out:
            await run_server(tmp_path, first)
            await out.reached(1)
        assert (tmp_path / policy.DATABASE_FILE_NAME).exists()
        await run_server(tmp_path, second)

    asyncio.run(scenario())


def test_the_wrong_password_after_a_restart_is_still_refused(tmp_path: Path) -> None:
    async def first(port: int, clock: ManualClock, gate: TickGate) -> None:
        (await login(port, "BOB,right")).writer.close()

    async def second(port: int, clock: ManualClock, gate: TickGate) -> None:
        c = await connect(port)
        await c.send("HHH\rBOB,wrong\r<Not Telling!>\rM 1380;3\r")
        await c.expect(b"WRONG PASSWORD")

    async def scenario() -> None:
        await run_server(tmp_path, first)
        await run_server(tmp_path, second)

    asyncio.run(scenario())
