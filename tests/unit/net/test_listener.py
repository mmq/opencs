"""Listener: limits and shutdown over real loopback sockets (no sleeping)."""

from __future__ import annotations

import asyncio
import logging
import socket

import pytest

from fixtures.streams import until, wait
from opencs import policy
from opencs.clock import ManualClock
from opencs.connlog import ConnectionContext
from opencs.net.connection import ConnectionIO
from opencs.net.listener import (
    ConnectionLimits,
    Listener,
    RefusalLog,
    enable_keepalive,
    normalize_address,
    subnet_of,
)


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
        listener = Listener(
            factory, ManualClock(), limits=ConnectionLimits(total=2, per_address=10)
        )
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
        listener = Listener(
            factory, ManualClock(), limits=ConnectionLimits(total=10, per_address=1)
        )
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


class StuckConnection:
    """A connection that ignores ``close`` and ends only when aborted (a peer that never reads,
    or anything else that does not unwind)."""

    def __init__(self, *, obeys_abort: bool = True) -> None:
        self.finished = asyncio.Event()
        self.closed_with: list[str] = []
        self.aborted_with: list[str] = []
        self._obeys_abort = obeys_abort

    def close(self, reason: str) -> None:
        self.closed_with.append(reason)

    def abort(self, reason: str) -> None:
        self.aborted_with.append(reason)
        if self._obeys_abort:
            self.finished.set()


def test_stop_aborts_what_outlasts_the_deadline_and_returns() -> None:
    async def scenario() -> tuple[StuckConnection, StuckConnection]:
        listener = Listener(factory, ManualClock())
        await listener.start("127.0.0.1", 0)
        stuck, kind = StuckConnection(), StuckConnection()
        kind.finished.set()  # this one closes at once
        listener._connections[1] = stuck  # type: ignore[assignment]
        listener._connections[2] = kind  # type: ignore[assignment]
        await wait(listener.stop(deadline=0.0, grace=1.0))
        return stuck, kind

    stuck, kind = asyncio.run(scenario())
    assert stuck.closed_with == ["server shutting down"] and kind.closed_with == stuck.closed_with
    assert stuck.aborted_with == ["shutdown deadline"]


def test_stop_does_not_wait_for_ever_on_a_connection_that_ignores_abort() -> None:
    async def scenario() -> None:
        listener = Listener(factory, ManualClock())
        await listener.start("127.0.0.1", 0)
        listener._connections[1] = StuckConnection(obeys_abort=False)  # type: ignore[assignment]
        await wait(listener.stop(deadline=0.0, grace=0.0))

    asyncio.run(scenario())


def test_a_connection_that_closes_in_time_is_not_aborted() -> None:
    async def scenario() -> StuckConnection:
        listener = Listener(factory, ManualClock())
        await listener.start("127.0.0.1", 0)
        quick = StuckConnection()
        quick.close = lambda reason: quick.finished.set()  # type: ignore[method-assign]
        listener._connections[1] = quick  # type: ignore[assignment]
        await wait(listener.stop(deadline=5.0))
        return quick

    assert asyncio.run(scenario()).aborted_with == []


@pytest.mark.parametrize(
    ("address", "normal", "subnet"),
    [
        ("203.0.113.7", "203.0.113.7", "203.0.113.0/24"),
        ("::ffff:203.0.113.7", "203.0.113.7", "203.0.113.0/24"),
        ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64"),
        ("?", "?", "?"),
    ],
)
def test_addresses_are_keyed_as_one_host_and_grouped_by_subnet(
    address: str, normal: str, subnet: str
) -> None:
    assert normalize_address(address) == normal
    assert subnet_of(normal) == subnet


def test_per_subnet_limit() -> None:
    async def scenario() -> list[bytes]:
        listener = Listener(
            factory, ManualClock(), limits=ConnectionLimits(total=10, per_address=10, per_subnet=1)
        )
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


class LogsIn(Greeter):
    """A Greeter that counts as logged in once it receives "login"."""

    def __init__(self, io: ConnectionIO, context: ConnectionContext) -> None:
        super().__init__(io)
        self.context = context

    def feed(self, data: bytes) -> None:
        if data.startswith(b"login"):
            self.context.authenticated = True
        super().feed(data)


def logs_in(io: ConnectionIO, context: ConnectionContext) -> LogsIn:
    return LogsIn(io, context)


def test_an_address_may_have_only_so_many_connections_not_logged_in() -> None:
    async def scenario() -> list[bytes]:
        listener = Listener(
            logs_in,
            ManualClock(),
            limits=ConnectionLimits(per_address=10, unauthenticated_per_address=1),
        )
        r1, w1 = await open_to(listener)
        port = listener._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        got = [await wait(r1.readexactly(2))]
        r2, w2 = await wait(asyncio.open_connection("127.0.0.1", port))
        got.append(await wait(r2.read()))  # refused: the first has not logged in
        w1.write(b"login")
        await until(lambda: listener._logging_in() == [], "the first logged in")
        r3, w3 = await wait(asyncio.open_connection("127.0.0.1", port))
        got.append(await wait(r3.readexactly(2)))  # now there is room
        await listener.stop()
        for w in (w1, w2, w3):
            w.close()
        return got

    assert asyncio.run(scenario()) == [b"hi", b"", b"hi"]


def test_past_the_total_not_logged_in_the_oldest_such_connection_is_cut() -> None:
    async def scenario() -> bytes:
        listener = Listener(
            logs_in,
            ManualClock(),
            limits=ConnectionLimits(
                per_address=10, unauthenticated=2, unauthenticated_per_address=10
            ),
        )
        r1, w1 = await open_to(listener)
        port = listener._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        await wait(r1.readexactly(2))
        r2, w2 = await wait(asyncio.open_connection("127.0.0.1", port))
        await wait(r2.readexactly(2))
        r3, w3 = await wait(asyncio.open_connection("127.0.0.1", port))
        await wait(r3.readexactly(2))
        cut = await wait(r1.read())  # the oldest is cut for the newest: end of stream
        await until(lambda: listener.connection_count == 2, "the oldest gone")
        w2.write(b"login")  # the other two are still served
        await until(lambda: len(listener._logging_in()) == 1, "the second logged in")
        await listener.stop()
        for w in (w1, w2, w3):
            w.close()
        return cut

    assert asyncio.run(scenario()) == b""


def test_an_address_that_connects_too_often_is_refused_until_its_rate_refills() -> None:
    async def scenario() -> list[bytes]:
        clock = ManualClock()
        listener = Listener(
            factory,
            clock,
            limits=ConnectionLimits(connect_rate=(2, 0.5), unauthenticated_per_address=10),
        )
        r1, w1 = await open_to(listener)
        port = listener._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        got = [await wait(r1.readexactly(2))]
        r2, w2 = await wait(asyncio.open_connection("127.0.0.1", port))
        got.append(await wait(r2.readexactly(2)))
        r3, w3 = await wait(asyncio.open_connection("127.0.0.1", port))
        got.append(await wait(r3.read()))  # the third in a row is refused
        clock.advance(2.0)  # one token back
        r4, w4 = await wait(asyncio.open_connection("127.0.0.1", port))
        got.append(await wait(r4.readexactly(2)))
        await listener.stop()
        for w in (w1, w2, w3, w4):
            w.close()
        return got

    assert asyncio.run(scenario()) == [b"hi", b"hi", b"", b"hi"]


def test_refusals_are_logged_once_a_minute_per_address_and_summed_up(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    refusals = RefusalLog(clock)
    with caplog.at_level(logging.WARNING, logger="opencs.net"):
        for _ in range(50):
            refusals.refused("203.0.113.7", "connecting too often")
        refusals.refused("198.51.100.1", "4 from that address")
        clock.advance(policy.REFUSAL_LOG_INTERVAL)
        refusals.refused("203.0.113.7", "connecting too often")
    assert [r.getMessage() for r in caplog.records] == [
        "refusing 203.0.113.7: connecting too often",
        "refusing 198.51.100.1: 4 from that address",
        "refusing 203.0.113.7: connecting too often (and 49 more since the last line)",
    ]


def test_the_refusal_log_remembers_a_bounded_number_of_addresses() -> None:
    clock = ManualClock()
    refusals = RefusalLog(clock)
    for n in range(policy.MAX_TRACKED_SOURCES + 10):
        refusals.refused(f"10.{n // 65536}.{n // 256 % 256}.{n % 256}", "x")
    assert len(refusals._last) <= policy.MAX_TRACKED_SOURCES
