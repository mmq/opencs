"""The TCP listener: accepts connections within limits and runs one ``Connection`` each.

DESIGN §3.3, §5. Over-limit connections are closed at once. Shutdown stops accepting and closes
every connection, letting each flush what it has queued.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import itertools
import logging
import socket
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

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


def normalize_address(address: str) -> str:
    """``address`` as the limits key it: an IPv4-mapped IPv6 address (``::ffff:1.2.3.4``, from a
    dual-stack socket) as the IPv4 address it is, so the same host is never two sources."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return str(ip.ipv4_mapped)
    return str(ip)


def subnet_of(address: str) -> str:
    """The network ``address`` is limited with (``policy.SUBNET_PREFIX_*``): a host with a block of
    addresses must not get a block of allowances."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    prefix = policy.SUBNET_PREFIX_V4 if ip.version == 4 else policy.SUBNET_PREFIX_V6
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))


@dataclass(frozen=True, slots=True)
class ConnectionLimits:
    """What the listener admits (``policy.MAX_CONNECTIONS*``, ``policy.MAX_UNAUTHENTICATED*``)."""

    total: int = policy.MAX_CONNECTIONS
    per_address: int = policy.MAX_CONNECTIONS_PER_ADDRESS
    per_subnet: int = policy.MAX_CONNECTIONS_PER_SUBNET
    unauthenticated: int = policy.MAX_UNAUTHENTICATED
    unauthenticated_per_address: int = policy.MAX_UNAUTHENTICATED_PER_ADDRESS


class Listener:
    """Accept, count and track connections.

    A new connection is refused at once past the total, per-address or per-subnet limit, or when its
    address already has ``limits.unauthenticated_per_address`` connections that have not logged in.
    Past ``limits.unauthenticated`` such connections in all, the oldest of them is cut to make room,
    so connections that never log in can hold the logon only until honest ones arrive.
    """

    def __init__(
        self,
        factory: SessionFactory,
        clock: Clock,
        *,
        limits: ConnectionLimits = ConnectionLimits(),  # noqa: B008  (frozen, so shared safely)
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        capture: CaptureFactory | None = None,
    ) -> None:
        self._capture = capture
        self._factory = factory
        self._clock = clock
        self._sleep = sleep
        self._limits = limits
        self._ids = itertools.count(1)
        self._connections: dict[int, Connection] = {}  # oldest first
        self._per_address: Counter[str] = Counter()
        self._per_subnet: Counter[str] = Counter()
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
        address = normalize_address(str(peer[0])) if peer else "?"
        subnet = subnet_of(address)
        if self._stopping:
            await self._refuse(writer)
            return
        if (reason := self._over_limit(address, subnet)) is not None:
            log.warning("refusing %s: %s", address, reason)
            await self._refuse(writer)
            return
        self._make_room_to_log_in()
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
        self._per_subnet[subnet] += 1
        log.info("[conn=%d -] accepted from %s", conn_id, address)
        try:
            await connection.run()
        finally:
            del self._connections[conn_id]
            self._per_address[address] -= 1
            if not self._per_address[address]:
                del self._per_address[address]
            self._per_subnet[subnet] -= 1
            if not self._per_subnet[subnet]:
                del self._per_subnet[subnet]

    def _over_limit(self, address: str, subnet: str) -> str | None:
        """Why a new connection from ``address`` is refused, None if it is not."""
        if len(self._connections) >= self._limits.total:
            return f"{len(self._connections)} connections"
        if self._per_address[address] >= self._limits.per_address:
            return f"{self._per_address[address]} from that address"
        if self._per_subnet[subnet] >= self._limits.per_subnet:
            return f"{self._per_subnet[subnet]} from {subnet}"
        logging_in = sum(1 for c in self._logging_in() if c.context.address == address)
        if logging_in >= self._limits.unauthenticated_per_address:
            return f"{logging_in} from that address not logged in"
        return None

    def _logging_in(self) -> list[Connection]:
        """The connections that have not logged in, oldest first."""
        return [c for c in self._connections.values() if not c.context.authenticated]

    def _make_room_to_log_in(self) -> None:
        """At ``limits.unauthenticated`` connections not logged in, cut the oldest of them."""
        waiting = self._logging_in()
        if len(waiting) >= self._limits.unauthenticated:
            oldest = waiting[0]
            log.warning(
                "%d connections not logged in; cutting the oldest, from %s",
                len(waiting),
                oldest.context.address,
            )
            oldest.abort("making room: too many connections not logged in")

    @staticmethod
    async def _refuse(writer: asyncio.StreamWriter) -> None:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
