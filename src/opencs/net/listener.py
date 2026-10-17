"""The TCP listener: accepts connections within limits and runs one ``Connection`` each.

DESIGN §3.3, §5. Over-limit connections are closed at once. Shutdown stops accepting and closes
every connection, letting each flush what it has queued.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
from collections import Counter

from opencs import policy
from opencs.clock import Clock
from opencs.connlog import ConnectionContext
from opencs.net.connection import Connection, SessionFactory

log = logging.getLogger("opencs.net")


class Listener:
    """Accept, count and track connections."""

    def __init__(
        self,
        factory: SessionFactory,
        clock: Clock,
        *,
        max_connections: int = policy.MAX_CONNECTIONS,
        max_per_address: int = policy.MAX_CONNECTIONS_PER_ADDRESS,
    ) -> None:
        self._factory = factory
        self._clock = clock
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

    async def stop(self) -> None:
        """Stop accepting, close every connection (flushing), and wait for them to end."""
        self._stopping = True  # a connection accepted but not yet handed to us is refused
        if self._server is not None:
            self._server.close()
        connections = list(self._connections.values())
        for connection in connections:
            connection.close("server shutting down")
        for connection in connections:
            await connection.finished.wait()
        if self._server is not None:
            await self._server.wait_closed()

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
        conn_id = next(self._ids)
        connection = Connection(
            reader, writer, self._factory, self._clock, ConnectionContext(conn_id)
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
