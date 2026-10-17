"""Listener: limits and shutdown over real loopback sockets (no sleeping)."""

from __future__ import annotations

import asyncio
import socket

import pytest

from fixtures.streams import until, wait
from opencs.clock import ManualClock
from opencs.connlog import ConnectionContext
from opencs.net.connection import ConnectionIO
from opencs.net.listener import Listener, enable_keepalive


class Greeter:
    """Says hello when created; closes when it receives "bye"."""

    def __init__(self, io: ConnectionIO) -> None:
        self.io = io
        io.send_raw(b"hi")

    def feed(self, data: bytes) -> None:
        if data.startswith(b"bye"):
            self.io.close("asked")

    def tick(self, now: float) -> None:
        pass

    def closed(self) -> None:
        pass


def factory(io: ConnectionIO, context: ConnectionContext) -> Greeter:
    return Greeter(io)


async def open_to(listener: Listener) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    server = await listener.start("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return await wait(asyncio.open_connection("127.0.0.1", port))


def test_total_connection_limit_refuses_the_extra_one_at_once() -> None:
    async def scenario() -> list[bytes]:
        listener = Listener(factory, ManualClock(), max_connections=2, max_per_address=10)
        r1, w1 = await open_to(listener)
        port = listener._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        got = [await wait(r1.readexactly(2))]
        r2, w2 = await wait(asyncio.open_connection("127.0.0.1", port))
        got.append(await wait(r2.readexactly(2)))
        r3, w3 = await wait(asyncio.open_connection("127.0.0.1", port))
        got.append(await wait(r3.read()))  # closed with nothing sent
        assert listener.connection_count == 2
        # A slot frees up when a client leaves.
        w1.write(b"bye")
        assert await wait(r1.read()) == b""
        await until(lambda: listener.connection_count == 1, "slot freed")
        r4, w4 = await wait(asyncio.open_connection("127.0.0.1", port))
        got.append(await wait(r4.readexactly(2)))
        await listener.stop()
        for w in (w1, w2, w3, w4):
            w.close()
        return got

    assert asyncio.run(scenario()) == [b"hi", b"hi", b"", b"hi"]


def test_per_address_limit() -> None:
    async def scenario() -> list[bytes]:
        listener = Listener(factory, ManualClock(), max_connections=10, max_per_address=1)
        r1, w1 = await open_to(listener)
        port = listener._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        got = [await wait(r1.readexactly(2))]
        r2, w2 = await wait(asyncio.open_connection("127.0.0.1", port))
        got.append(await wait(r2.read()))
        await listener.stop()
        w1.close()
        w2.close()
        return got

    assert asyncio.run(scenario()) == [b"hi", b""]


def test_stop_closes_every_connection_after_flushing() -> None:
    async def scenario() -> tuple[bytes, bytes, int]:
        listener = Listener(factory, ManualClock())
        r1, w1 = await open_to(listener)
        port = listener._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        r2, w2 = await wait(asyncio.open_connection("127.0.0.1", port))
        await until(lambda: listener.connection_count == 2, "both accepted")
        await wait(listener.stop())
        a, b = await wait(r1.read()), await wait(r2.read())  # everything, then EOF
        w1.close()
        w2.close()
        return a, b, listener.connection_count

    a, b, remaining = asyncio.run(scenario())
    assert (a, b, remaining) == (b"hi", b"hi", 0)


def test_enable_keepalive_sets_so_keepalive() -> None:
    with socket.socket() as sock:
        enable_keepalive(sock)
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE)
    enable_keepalive(None)  # a transport with no socket is tolerated


def test_accepted_sockets_get_keepalive(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []
    monkeypatch.setattr("opencs.net.listener.enable_keepalive", seen.append)

    async def scenario() -> None:
        listener = Listener(factory, ManualClock())
        reader, writer = await open_to(listener)
        await wait(reader.readexactly(2))
        writer.close()
        await listener.stop()

    asyncio.run(scenario())
    assert len(seen) == 1 and seen[0] is not None
