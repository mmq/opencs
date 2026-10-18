"""The TCP listener: accepts connections within limits and runs one ``Connection`` each.

DESIGN §3.3, §5. Over-limit connections are closed at once. Shutdown stops accepting and closes
every connection, letting each flush what it has queued.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import socket
from collections import Counter
from collections.abc import Awaitable, Callable

from opencs import policy
from opencs.clock import Clock
from opencs.connlog import ConnectionContext
from opencs.net.capture import CaptureFactory
from opencs.net.connection import Connection, SessionFactory

log = logging.getLogger("opencs.net")


def enable_keepalive(sock: socket.socket | None) -> None:
    """Turn on TCP keepalive with the policy's timing, where the platform has the options."""
    if sock is None:
        return
    options = (
        ("TCP_KEEPIDLE", policy.KEEPALIVE_IDLE),  # macOS spells this TCP_KEEPALIVE
        ("TCP_KEEPALIVE", policy.KEEPALIVE_IDLE),
        ("TCP_KEEPINTVL", policy.KEEPALIVE_INTERVAL),
        ("TCP_KEEPCNT", policy.KEEPALIVE_PROBES),
    )
    with contextlib.suppress(OSError):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for name, value in options:
            option = getattr(socket, name, None)
            if option is not None:
                sock.setsockopt(socket.IPPROTO_TCP, option, value)


class Listener:
    """Accept, count and track connections."""

    def __init__(
        self,
        factory: SessionFactory,
        clock: Clock,
        *,
        max_connections: int = policy.MAX_CONNECTIONS,
        max_per_address: int = policy.MAX_CONNECTIONS_PER_ADDRESS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        capture: CaptureFactory | None = None,
    ) -> None:
        self._capture = capture
        self._factory = factory
        self._clock = clock
        self._sleep = sleep
        self._max = max_connections
        self._max_per_address = max_per_address
        self._ids = itertools.count(1)
        self._connections: dict[int, Connection] = {}
        self._per_address: Counter[str] = Counter()
        self._server: asyncio.Server | None = None
        self._stopping = False

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    async def start(self, host: str, port: int) -> asyncio.Server:
        """Bind and start accepting."""
        self._server = await asyncio.start_server(self._accept, host, port)
        return self._server

    def stop_accepting(self) -> None:
        """Close the listening sockets; a connection accepted but not yet handed to us is
        refused."""
        self._stopping = True
        if self._server is not None:
            self._server.close()

    async def stop(
        self,
        deadline: float = policy.SHUTDOWN_DEADLINE,
        grace: float = policy.SHUTDOWN_ABORT_GRACE,
    ) -> None:
        """Stop accepting, close every connection (each flushes what it has queued) and wait for
        them to end, at most ``deadline`` seconds; whatever is left is aborted and given ``grace``
        more. A client that never reads, or anything else stuck, cannot hold a shutdown up."""
        self.stop_accepting()
        connections = list(self._connections.values())
        for connection in connections:
            connection.close("server shutting down")
        waits = [asyncio.ensure_future(c.finished.wait()) for c in connections]
        if waits:
            _, pending = await asyncio.wait(waits, timeout=deadline)
            if pending:
                log.warning(
                    "%d connections did not close in %.1f s; aborting", len(pending), deadline
                )
                for connection in connections:
                    connection.abort("shutdown deadline")
                _, pending = await asyncio.wait(pending, timeout=grace)
                for wait in pending:
                    wait.cancel()
        if self._server is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._server.wait_closed(), grace)

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        address = str(peer[0]) if peer else "?"
        if self._stopping:
            await self._refuse(writer)
            return
        if len(self._connections) >= self._max:
            log.warning("refusing %s: %d connections", address, len(self._connections))
            await self._refuse(writer)
            return
        if self._per_address[address] >= self._max_per_address:
            log.warning("refusing %s: %d from that address", address, self._per_address[address])
            await self._refuse(writer)
            return
        enable_keepalive(writer.get_extra_info("socket"))
        conn_id = next(self._ids)
        context = ConnectionContext(conn_id, address)
        connection = Connection(
            reader,
            writer,
            self._factory,
            self._clock,
            context,
            sleep=self._sleep,
            capture=None
            if self._capture is None
            else self._capture.new(context, self._clock.now()),
        )
        self._connections[conn_id] = connection
        self._per_address[address] += 1
        log.info("[conn=%d -] accepted from %s", conn_id, address)
        try:
            await connection.run()
        finally:
            del self._connections[conn_id]
            self._per_address[address] -= 1
            if not self._per_address[address]:
                del self._per_address[address]

    @staticmethod
    async def _refuse(writer: asyncio.StreamWriter) -> None:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
