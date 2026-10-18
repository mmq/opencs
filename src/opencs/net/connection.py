"""One socket: a read pump, a write pump with a bounded outbox, and the tag counter.

DESIGN §3.3 and §3.2. The connection knows nothing of phases or rules: it feeds bytes to a
``SessionHandler``, drains what the handler queues, and turns each queued ``ServerMessage`` into a
framed packet at the send boundary, where it assigns the tag (I §4.2, ADR 5). A slow client
fills only its own outbox; when that overflows, that client is cut and nobody else notices.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from opencs import policy
from opencs.clock import Clock
from opencs.connlog import ConnectionContext, ConnectionLog
from opencs.messages import ServerMessage, server_payload
from opencs.net.capture import Capture
from opencs.wire.codec import BYTE_MAX, BYTE_MIN
from opencs.wire.frame import build_frame


class SessionHandler(Protocol):
    """What a connection drives: bytes in, time passing, the socket gone."""

    def feed(self, data: bytes) -> None: ...

    def tick(self, now: float) -> None: ...

    def closed(self) -> None: ...


class ConnectionIO(Protocol):
    """What a session is given to write with (the same shape as ``session.io.SessionIO``; the net
    layer may not import the session layer, so it is restated here)."""

    def send_raw(self, data: bytes) -> None: ...

    def send(self, msg: ServerMessage) -> None: ...

    def send_first(self, msg: ServerMessage) -> None: ...

    def close(self, reason: str) -> None: ...


SessionFactory = Callable[[ConnectionIO, ConnectionContext], SessionHandler]


class StreamReader(Protocol):
    async def read(self, n: int, /) -> bytes: ...


class StreamWriter(Protocol):
    """The part of ``asyncio.StreamWriter`` a connection uses."""

    @property
    def transport(self) -> Any: ...

    def write(self, data: bytes, /) -> None: ...

    async def drain(self) -> None: ...

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...


class _Close:
    """Queued after everything to send: the write pump flushes up to it, then stops."""


_CLOSE = _Close()

type Outbound = bytes | ServerMessage | _Close


class Connection:
    """The I/O of one client."""

    def __init__(  # noqa: PLR0913  (one argument per collaborator, each optional but the first five)
        self,
        reader: StreamReader,
        writer: StreamWriter,
        factory: SessionFactory,
        clock: Clock,
        context: ConnectionContext,
        *,
        outbox_limit: int = policy.OUTBOX_LIMIT,
        flush_timeout: float = policy.CLOSE_FLUSH_TIMEOUT,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        capture: Capture | None = None,
    ) -> None:
        self.context = context
        self._capture = capture
        self._reader = reader
        self._writer = writer
        self._factory = factory
        self._clock = clock
        self._limit = outbox_limit
        self._flush_timeout = flush_timeout
        self._sleep = sleep  # the session tick's pacing; tests pulse it by hand
        self._log = ConnectionLog(logging.getLogger("opencs.net"), context)
        self._outbox: deque[Outbound] = deque()
        self._wake = asyncio.Event()
        self._next_tag = BYTE_MIN
        self._closing = False
        self._aborted = False
        self._flush_timer: asyncio.TimerHandle | None = None
        self._session: SessionHandler | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self.finished = asyncio.Event()

    # --- what the session writes with ---------------------------------------------------------

    def send_raw(self, data: bytes) -> None:
        """Queue bytes as they are."""
        self._enqueue(data)

    def send(self, msg: ServerMessage) -> None:
        """Queue a message; its tag is assigned when it is written."""
        self._enqueue(msg)

    def send_first(self, msg: ServerMessage) -> None:
        """Queue a message ahead of everything already queued: the latency ping's reply, whose
        delay the client measures and shows (I §8.7)."""
        self._enqueue(msg, front=True)

    def close(self, reason: str) -> None:
        """Send what is queued, then close. A client that does not read is cut after
        ``CLOSE_FLUSH_TIMEOUT``."""
        if self._closing or self._aborted:
            return
        self._log.info("closing: %s", reason)
        self._closing = True
        self._outbox.append(_CLOSE)
        self._wake.set()
        self._flush_timer = asyncio.get_running_loop().call_later(
            self._flush_timeout, self.abort, "close did not flush in time"
        )

    def abort(self, reason: str) -> None:
        """Drop what is queued and cut the connection now."""
        if self._aborted:
            return
        self._log.warning("aborting: %s", reason)
        self._aborted = True
        self._outbox.clear()
        self._wake.set()
        with contextlib.suppress(Exception):
            self._writer.transport.abort()
        for task in self._tasks:  # a pump stuck in drain() on a dead peer must not hold us up
            task.cancel()

    def _enqueue(self, item: bytes | ServerMessage, *, front: bool = False) -> None:
        if self._closing or self._aborted:
            return
        if len(self._outbox) >= self._limit:
            self.abort(f"outbox over {self._limit} items")
            return
        if front:
            self._outbox.appendleft(item)
        else:
            self._outbox.append(item)
        self._wake.set()

    def encode(self, msg: ServerMessage) -> bytes:
        """The frame for ``msg``, with the next tag if its type is uppercase (I §4.2, II §7).

        The counter runs ``0x10 .. 0xff`` then wraps to ``0x10``; lowercase messages do not use
        it. It lives here and nowhere else.
        """
        tag = self._next_tag
        data = build_frame(server_payload(msg, tag))
        if msg.is_tagged():
            self._next_tag = BYTE_MIN if tag == BYTE_MAX else tag + 1
        return data

    # --- the three pumps ----------------------------------------------------------------------

    async def run(self) -> None:
        """Serve the connection until it ends; then release the session. Never raises."""
        try:
            self._session = self._factory(self, self.context)
        except Exception:
            self._log.exception("could not start a session")
            self._aborted = True
        try:
            if self._session is not None:
                self._tasks = [
                    asyncio.create_task(self._read_pump()),
                    asyncio.create_task(self._write_pump()),
                    asyncio.create_task(self._tick_pump()),
                ]
                await asyncio.wait(self._tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            await self._finish(self._tasks)

    async def _finish(self, tasks: list[asyncio.Task[None]]) -> None:
        if self._flush_timer is not None:
            self._flush_timer.cancel()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._session is not None:
            self._guard(self._session.closed)
        with contextlib.suppress(Exception):
            self._writer.close()
            await self._writer.wait_closed()
        if self._capture is not None:
            self._capture.close()
        self._log.info("closed")
        self.finished.set()

    async def _read_pump(self) -> None:
        while True:
            try:
                data = await self._reader.read(policy.READ_CHUNK)
            except (OSError, asyncio.IncompleteReadError) as exc:
                self._log.info("read failed: %s", exc)
                return
            if not data:
                self._log.info("client closed the connection")
                return
            if self._closing or self._aborted or self._session is None:
                continue  # closing: the session has stopped listening
            if self._capture is not None:
                self._capture.client_bytes(data, self._clock.now())
            self._guard(self._session.feed, data)
            self._guard(self._session.tick, self._clock.now())

    async def _tick_pump(self) -> None:
        while True:
            await self._sleep(1 / policy.SESSION_TICK_HZ)
            if self._session is not None and not (self._closing or self._aborted):
                self._guard(self._session.tick, self._clock.now())

    async def _write_pump(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._outbox:
                item = self._outbox.popleft()
                if isinstance(item, _Close):
                    await self._drain()
                    return
                data = self._frame(item)
                if data:
                    if self._capture is not None:
                        self._capture.server_data(
                            data, framed=not isinstance(item, bytes), now=self._clock.now()
                        )
                    self._writer.write(data)
            if self._aborted:
                return
            if not await self._drain():
                return

    async def _drain(self) -> bool:
        try:
            await self._writer.drain()
        except OSError as exc:
            self._log.info("write failed: %s", exc)
            return False
        return True

    def _frame(self, item: bytes | ServerMessage) -> bytes:
        if isinstance(item, bytes):
            return item
        try:
            return self.encode(item)
        except Exception:
            self._log.exception("could not encode %s; dropped", type(item).__name__)
            return b""

    def _guard[**P](self, fn: Callable[P, None], *args: P.args, **kwargs: P.kwargs) -> None:
        """Run a session handler; if it raises, log and cut this connection (DESIGN §5)."""
        try:
            fn(*args, **kwargs)
        except Exception:
            self._log.exception("session handler raised; closing the connection")
            self.abort("session handler raised")
