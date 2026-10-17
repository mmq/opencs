"""Scripted clients on real loopback sockets, for tests that run the whole server.

Nothing here waits for time. A read waits on the socket (the server answers when it has the bytes);
the server's tickers wait on a ``TickGate`` that the test pulses; when a test needs to know the
server has consumed something, it waits on a ``LogWatch`` for the line the server logs about it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from opencs.app import serve
from opencs.clock import ManualClock
from opencs.config import AppConfig, load_config
from opencs.messages import ClientMessage, ServerMessage, client_payload, decode_server, server
from opencs.session.phases import Services
from opencs.store.base import Stores
from opencs.store.memory import memory_stores
from opencs.wire.frame import Frame, FrameReader, build_frame

TIMEOUT = 5.0
SHIPPED = Path(__file__).parents[2] / "config" / "server.toml"


def local_config() -> AppConfig:
    """The shipped config on an ephemeral loopback port."""
    config = load_config(SHIPPED)
    return replace(config, server=replace(config.server, host="127.0.0.1", port=0))


class TickGate:
    """The tickers' ``sleep``: every waiting ticker wakes when the test calls ``pulse``. One pulse
    is one tick of every city and every session; the test advances its ``ManualClock`` first."""

    def __init__(self) -> None:
        self._waiting: list[asyncio.Future[None]] = []

    async def sleep(self, seconds: float) -> None:
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiting.append(waiter)
        await waiter

    def pulse(self) -> None:
        waiting, self._waiting = self._waiting, []
        for waiter in waiting:
            if not waiter.done():
                waiter.set_result(None)


class LogWatch(logging.Handler):
    """Counts server log lines that contain a text, so a test can wait until the server has acted
    on what it sent: ``await watch.reached(n)`` (an event wait, not a sleep)."""

    def __init__(self, logger: str, contains: str) -> None:
        super().__init__()
        self._logger = logging.getLogger(logger)
        self._contains = contains
        self.count = 0
        self._changed = asyncio.Event()

    def __enter__(self) -> LogWatch:
        self._logger.addHandler(self)
        self._previous = self._logger.level
        self._logger.setLevel(logging.INFO)
        return self

    def __exit__(self, *exc: object) -> None:
        self._logger.removeHandler(self)
        self._logger.setLevel(self._previous)

    def emit(self, record: logging.LogRecord) -> None:
        if self._contains in record.getMessage():
            self.count += 1
            self._changed.set()

    async def reached(self, n: int) -> None:
        while self.count < n:
            self._changed.clear()
            await asyncio.wait_for(self._changed.wait(), TIMEOUT)


class Client:
    """A scripted client on a real loopback socket. Reads wait on the socket, never on time."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.buffer = b""
        self._frames = FrameReader()
        self._parsed: list[Frame] = []

    async def send(self, data: bytes | str) -> None:
        self.writer.write(data.encode("latin-1") if isinstance(data, str) else data)
        await asyncio.wait_for(self.writer.drain(), TIMEOUT)

    async def _receive(self) -> None:
        """Read once; keep the bytes for ``expect`` and any frames in them for ``expect_frame``."""
        data = await asyncio.wait_for(self.reader.read(4096), TIMEOUT)
        assert data, f"server closed; had {self.buffer!r}"
        self.buffer += data
        self._parsed += self._frames.feed(data)

    async def expect_frame(self, letter: str) -> ServerMessage:
        """Read until a server frame of this type letter has arrived; return it decoded. The whole
        wait has one deadline, so a frame that never comes fails the test with the frame types
        that did, even while other frames keep arriving."""
        want = letter.encode()

        async def find() -> ServerMessage:
            while True:
                for i, frame in enumerate(self._parsed):
                    if frame.payload[:1] == want:
                        del self._parsed[: i + 1]
                        return decode_server(frame.payload)[0]
                await self._receive()

        try:
            return await asyncio.wait_for(find(), TIMEOUT)
        except TimeoutError:
            seen = [f.payload[:1] for f in self._parsed]
            raise AssertionError(f"no {letter!r} frame; unclaimed frames: {seen!r}") from None

    async def expect(self, needle: bytes) -> bytes:
        """Read until ``needle`` has arrived; return everything up to and including it. One
        deadline for the whole wait."""

        async def find() -> None:
            while needle not in self.buffer:
                await self._receive()

        try:
            await asyncio.wait_for(find(), TIMEOUT)
        except TimeoutError:
            raise AssertionError(f"no {needle!r}; buffered: {self.buffer!r}") from None
        end = self.buffer.index(needle) + len(needle)
        got, self.buffer = self.buffer[:end], self.buffer[end:]
        return got


async def start(
    config: AppConfig,
    *,
    clock: ManualClock | None = None,
    services: Services | None = None,
    stores: Stores | None = None,
    tick_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[asyncio.Event, asyncio.Task[None], int]:
    stop = asyncio.Event()
    port_future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(
        serve(
            config,
            stop=stop,
            ready=port_future.set_result,
            clock=clock,
            services=services,
            stores=stores or (None if services else memory_stores()),
            tick_sleep=tick_sleep,
        )
    )
    return stop, task, await asyncio.wait_for(port_future, TIMEOUT)


async def connect(port: int) -> Client:
    reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), TIMEOUT)
    return Client(reader, writer)


def frame(msg: ClientMessage) -> bytes:
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
