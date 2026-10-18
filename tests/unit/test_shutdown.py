"""Graceful shutdown (M12, DESIGN §6): everyone is told, taken out of their game and saved, and
the server stops within its deadline."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from fixtures.loopback import (
    TIMEOUT,
    Client,
    TickGate,
    connect,
    enter,
    frame,
    local_config,
    start,
)
from fixtures.pods import report
from opencs import policy
from opencs.clock import ManualClock
from opencs.config import AppConfig
from opencs.control import SHUTDOWN_NOTICE
from opencs.messages import client, server
from opencs.store.base import ProgressRecord, Stores
from opencs.store.sqlite import SqliteStore, open_stores

BAYS = (3, 2, 2, 1, 1, 0, 0, 0, 0)
ACCOUNT, KEY = "SAVER,PW", "acct:SAVER"  # guests are not saved (II §11.3)


def config_in(data_dir: Path) -> AppConfig:
    config = local_config()
    return replace(config, server=replace(config.server, data_dir=data_dir))


class FlakyProgress:
    """A progress store over the SQLite file that refuses saves while ``down``: the one way a
    record is still unsaved when a shutdown comes (every report is otherwise written at once)."""

    def __init__(self, db: SqliteStore) -> None:
        self._db = db
        self.down = False

    def load(self, identity: str) -> ProgressRecord | None:
        return self._db.load(identity)

    def save(self, identity: str, record: ProgressRecord) -> None:
        if self.down:
            raise OSError("disk unavailable")
        self._db.save(identity, record)


def saved(data_dir: Path) -> tuple[int, int, tuple[int, ...]] | None:
    """What a new app would find for the player: (score, bay points, bays)."""
    stores, db = open_stores(data_dir)
    try:
        record = stores.progress.load(KEY)
        return None if record is None else (record.score, record.bay_points, record.bays)
    finally:
        db.close()


async def play_with_the_store_down(
    data_dir: Path, *, deadline: float = policy.SHUTDOWN_DEADLINE
) -> tuple[Client, asyncio.Event, asyncio.Task[None]]:
    """A server whose store goes down after the first report; a player who then reports bays and
    SCORE (which cannot be saved) and a ping, so the reply shows the server has consumed them.
    The store is back up when this returns, with nothing yet written."""
    _, db = open_stores(data_dir)
    flaky = FlakyProgress(db)
    stores = Stores(db, flaky)
    clock, gate = ManualClock(1000.0), TickGate()
    stop, task, port = await start(
        config_in(data_dir),
        clock=clock,
        stores=stores,
        tick_sleep=gate.sleep,
        shutdown_deadline=deadline,
    )
    c, _ = await enter(port, "RED", ACCOUNT)
    await c.send(frame(report(4)))
    await c.expect_frame("A")
    flaky.down = True
    await c.send(frame(client.Bays(BAYS, 2)))
    await c.send(frame(client.Status(777, 2, 88)))
    await c.send(frame(client.Ping()))
    await c.expect_frame("E")
    flaky.down = False
    assert (
        saved(data_dir) is None
    )  # the report is waiting for a retry that the clock will not allow
    return c, stop, task


def test_shutdown_saves_a_player_in_game_and_a_new_app_shows_the_record(tmp_path: Path) -> None:
    async def scenario() -> None:
        c, stop, task = await play_with_the_store_down(tmp_path)
        stop.set()
        # The player is told, then sent back to the Chamber with the unprompted 'C' (I §5.5, I
        # §8.5).
        line = await c.expect_frame("a")
        assert line == server.Chat(100, 0, SHUTDOWN_NOTICE.encode())
        assert await c.expect_frame("C") == server.QuitAck()
        await asyncio.wait_for(task, TIMEOUT)
        assert saved(tmp_path) == (777, 88, BAYS)
        c.writer.close()

    asyncio.run(asyncio.wait_for(scenario(), 4 * TIMEOUT))


def test_a_player_who_never_reads_cannot_hold_the_shutdown_and_is_still_saved(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        c, stop, task = await play_with_the_store_down(tmp_path, deadline=0.0)  # the abort path
        stop.set()  # and the client reads nothing more
        await asyncio.wait_for(task, TIMEOUT)
        c.writer.close()
        assert saved(tmp_path) == (777, 88, BAYS)  # saved before any connection was waited for

    asyncio.run(asyncio.wait_for(scenario(), 4 * TIMEOUT))


def test_a_pilot_in_the_chamber_is_told_and_the_connection_closes() -> None:
    async def scenario() -> None:
        stop, task, port = await start(local_config())
        c = await connect(port)
        await c.send("HHH\rJOINGENIE\r<Not Telling!>\rM 1380;3\r")
        await c.expect(b"\x05PRESS RETURN\r")
        await c.send("\r")
        await c.expect(b"\x05A\r")
        await c.expect(b"TYPE /HELP FOR COMMANDS.\r\x05D0\r")
        stop.set()
        got = await c.expect(b"\x05D0\r")
        assert b"*** SERVER SHUTTING DOWN\r" in got  # the announcement
        assert b"SERVER SHUTTING DOWN\r\x05D0\r" in got  # and the removal line
        await asyncio.wait_for(task, TIMEOUT)
        c.writer.close()

    asyncio.run(asyncio.wait_for(scenario(), 4 * TIMEOUT))


def test_the_deadline_is_a_policy_value_longer_than_the_flush_timeout() -> None:
    assert policy.SHUTDOWN_DEADLINE > policy.CLOSE_FLUSH_TIMEOUT
