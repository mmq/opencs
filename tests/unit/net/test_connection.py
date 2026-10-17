"""Connection: tag counter, bounded outbox, error containment (DESIGN §3.3, §5; I §4.2, II §7)."""

from __future__ import annotations

import asyncio
from typing import Any

from fixtures.streams import FakeReader, FakeWriter, until, wait
from opencs import policy
from opencs.clock import ManualClock
from opencs.connlog import ConnectionContext
from opencs.messages import server
from opencs.net.connection import Connection, ConnectionIO
from opencs.wire.frame import FrameReader


class Handler:
    """A session stand-in: ``script`` is called with (io, data) on each feed."""

    def __init__(self, io: ConnectionIO, script: Any = None) -> None:
        self.io = io
        self.script = script
        self.fed: list[bytes] = []
        self.ticks = 0
        self.closed_calls = 0

    def feed(self, data: bytes) -> None:
        self.fed.append(data)
        if self.script:
            self.script(self.io, data)

    def tick(self, now: float) -> None:
        self.ticks += 1

    def closed(self) -> None:
        self.closed_calls += 1


def build(
    script: Any = None,
    *,
    limit: int = policy.OUTBOX_LIMIT,
    conn_id: int = 1,
    flush_timeout: float = policy.CLOSE_FLUSH_TIMEOUT,
) -> tuple[Connection, FakeReader, FakeWriter, list[Handler]]:
    reader, writer = FakeReader(), FakeWriter()
    handlers: list[Handler] = []

    def factory(io: ConnectionIO, context: ConnectionContext) -> Handler:
        handlers.append(Handler(io, script))
        return handlers[-1]

    connection = Connection(
        reader,
        writer,
        factory,
        ClockHolder.clock,
        ConnectionContext(conn_id),
        outbox_limit=limit,
        flush_timeout=flush_timeout,
    )
    return connection, reader, writer, handlers


class ClockHolder:
    clock = ManualClock()


def payloads(data: bytes) -> list[bytes]:
    reader = FrameReader()
    frames = reader.feed(data)
    assert reader.bad_frames == 0 and reader.skipped_bytes == 0
    return [f.payload for f in frames]


# --- tags ------------------------------------------------------------------------------------


def test_uppercase_messages_are_tagged_0x10_upward_and_wrap_after_0xff_to_0x10() -> None:
    def script(io: ConnectionIO, data: bytes) -> None:
        for _ in range(300):
            io.send(server.PingReply())
        io.close("done")

    async def scenario() -> list[bytes]:
        connection, reader, writer, _ = build(script, limit=1000)
        reader.push(b"go")
        await wait(connection.run())
        return payloads(writer.data)

    found = asyncio.run(scenario())
    assert len(found) == 300
    tags = [p[1] for p in found]
    assert tags[:3] == [0x10, 0x11, 0x12]
    assert tags[239] == 0xFF
    assert tags[240] == 0x10  # wrapped to 0x10, not to 0x00 or 0x11
    assert tags[241] == 0x11
    assert tags == [0x10 + i % 240 for i in range(300)]
    assert all(p[0:1] == b"E" and p[2:] == b"N" for p in found)


def test_lowercase_messages_get_no_tag_and_do_not_use_up_one() -> None:
    def script(io: ConnectionIO, data: bytes) -> None:
        io.send(server.PingReply())
        io.send(server.Chat(100, 0, b"hi"))
        io.send(server.Chat(100, 0, b"hi"))
        io.send(server.PingReply())
        io.close("done")

    async def scenario() -> list[bytes]:
        connection, reader, writer, _ = build(script)
        reader.push(b"go")
        await wait(connection.run())
        return payloads(writer.data)

    first, chat1, chat2, second = asyncio.run(scenario())
    assert first[1] == 0x10
    assert second[1] == 0x11  # the chats took no tag
    assert chat1 == chat2 == b"a" + bytes([0x10 + 100, 0x10, 0x12]) + b"hi"


def test_the_counter_is_per_connection() -> None:
    def script(io: ConnectionIO, data: bytes) -> None:
        io.send(server.PingReply())
        io.close("done")

    async def scenario() -> list[list[bytes]]:
        out = []
        for i in (1, 2):
            connection, reader, writer, _ = build(script, conn_id=i)
            reader.push(b"go")
            await wait(connection.run())
            out.append(payloads(writer.data))
        return out

    a, b = asyncio.run(scenario())
    assert a[0][1] == b[0][1] == 0x10


def test_frames_end_in_cr_and_raw_bytes_pass_through_in_order() -> None:
    def script(io: ConnectionIO, data: bytes) -> None:
        io.send_raw(b"\x05A\r")
        io.send(server.PingReply())
        io.send_raw(b"tail")
        io.close("done")

    async def scenario() -> bytes:
        connection, reader, writer, _ = build(script)
        reader.push(b"go")
        await wait(connection.run())
        return writer.data

    data = asyncio.run(scenario())
    assert data.startswith(b"\x05A\rCS")
    assert data.endswith(b"\x0dtail")


def test_send_first_goes_ahead_of_queued_traffic_and_is_tagged_when_written() -> None:
    def script(io: ConnectionIO, data: bytes) -> None:
        io.send_raw(b"first")
        io.send(server.ReleaseDrop(3))
        io.send_first(server.PingReply())
        io.close("done")

    async def scenario() -> bytes:
        connection, reader, writer, _ = build(script)
        reader.push(b"go")
        await wait(connection.run())
        return writer.data

    data = asyncio.run(scenario())
    frames = FrameReader().feed(data.replace(b"first", b""))
    assert [f.payload[0:1] for f in frames] == [b"E", b"A"]  # the reply, then the queued drop
    assert frames[0].payload[1] == 0x10  # tags follow the write order, not the call order
    assert frames[1].payload[1] == 0x11
    assert data.startswith(b"CS")  # ahead of the raw bytes queued before it, too
    assert data.index(b"first") > 0


# --- lifecycle -------------------------------------------------------------------------------


def test_the_session_is_fed_ticked_and_told_when_the_socket_ends() -> None:
    async def scenario() -> tuple[Connection, FakeWriter, Handler]:
        connection, reader, writer, handlers = build()
        reader.push(b"abc")
        reader.push(b"def")
        reader.eof()
        await wait(connection.run())
        return connection, writer, handlers[0]

    connection, writer, handler = asyncio.run(scenario())
    assert handler.fed == [b"abc", b"def"]
    assert handler.ticks >= 2  # a tick after every feed
    assert handler.closed_calls == 1
    assert writer.closed
    assert connection.finished.is_set()


def test_a_handler_that_raises_closes_this_connection_cleanly() -> None:
    def script(io: ConnectionIO, data: bytes) -> None:
        raise RuntimeError("bug")

    async def scenario() -> tuple[FakeWriter, Handler]:
        connection, reader, writer, handlers = build(script)
        reader.push(b"x")
        await wait(connection.run())
        return writer, handlers[0]

    writer, handler = asyncio.run(scenario())
    assert writer.closed
    assert writer.transport.aborted
    assert handler.closed_calls == 1


def test_a_factory_that_raises_closes_the_socket_without_raising() -> None:
    def factory(io: ConnectionIO, context: ConnectionContext) -> Handler:
        raise RuntimeError("bug")

    async def scenario() -> FakeWriter:
        reader, writer = FakeReader(), FakeWriter()
        connection = Connection(reader, writer, factory, ClockHolder.clock, ConnectionContext(1))
        await wait(connection.run())
        return writer

    assert asyncio.run(scenario()).closed


def test_close_flushes_what_was_queued_first_and_ignores_later_sends() -> None:
    def script(io: ConnectionIO, data: bytes) -> None:
        io.send_raw(b"one")
        io.close("bye")
        io.send_raw(b"two")
        io.close("again")

    async def scenario() -> FakeWriter:
        connection, reader, writer, _ = build(script)
        reader.push(b"x")
        reader.push(b"ignored after close")
        await wait(connection.run())
        return writer

    assert asyncio.run(scenario()).data == b"one"


def test_a_close_that_cannot_flush_is_cut_after_the_timeout() -> None:
    def script(io: ConnectionIO, data: bytes) -> None:
        io.send_raw(b"x")

    async def scenario() -> FakeWriter:
        connection, reader, writer, _ = build(script, flush_timeout=0)
        writer.gate.clear()  # the client has stopped reading
        reader.push(b"go")
        task = asyncio.create_task(connection.run())
        await wait(writer.drain_blocked.wait())
        connection.close("shutdown")
        await wait(task)
        return writer

    writer = asyncio.run(scenario())
    assert writer.transport.aborted


# --- the bounded outbox ----------------------------------------------------------------------


def test_the_outbox_holds_exactly_its_limit() -> None:
    def script(io: ConnectionIO, data: bytes) -> None:
        for _ in range(10):
            io.send_raw(b"x")
        io.close("done")

    async def scenario() -> FakeWriter:
        connection, reader, writer, _ = build(script, limit=10)  # the close marker does not count
        reader.push(b"go")
        await wait(connection.run())
        return writer

    writer = asyncio.run(scenario())
    assert writer.data == b"x" * 10
    assert not writer.transport.aborted


def test_overflow_closes_only_the_slow_clients_connection() -> None:
    limit = 8

    def flood(io: ConnectionIO, data: bytes) -> None:
        if data == b"first":
            io.send_raw(b"x")
        else:
            for _ in range(limit + 1):
                io.send_raw(b"x")

    def calm(io: ConnectionIO, data: bytes) -> None:
        io.send_raw(data)

    async def scenario() -> tuple[FakeWriter, FakeWriter, Handler]:
        slow, slow_reader, slow_writer, slow_handlers = build(flood, limit=limit, conn_id=1)
        fine, fine_reader, fine_writer, _ = build(calm, limit=limit, conn_id=2)
        slow_writer.gate.clear()  # it never reads
        slow_task = asyncio.create_task(slow.run())
        fine_task = asyncio.create_task(fine.run())
        slow_reader.push(b"first")
        await wait(slow_writer.drain_blocked.wait())  # the pump is stuck in drain
        slow_reader.push(b"second")  # now the backlog builds up in the outbox
        await wait(slow_task)  # overflow: the slow connection ends by itself
        fine_reader.push(b"hello")
        await until(lambda: fine_writer.data == b"hello", "the other client is still served")
        fine_reader.eof()
        await wait(fine_task)
        return slow_writer, fine_writer, slow_handlers[0]

    slow_writer, fine_writer, handler = asyncio.run(scenario())
    assert slow_writer.transport.aborted
    assert slow_writer.closed
    assert handler.closed_calls == 1
    assert not fine_writer.transport.aborted
    assert fine_writer.data == b"hello"


def test_overflow_at_exactly_limit_plus_one() -> None:
    def script(io: ConnectionIO, data: bytes) -> None:
        for _ in range(5):
            io.send_raw(b"x")

    async def scenario(limit: int) -> bool:
        connection, reader, writer, _ = build(script, limit=limit)
        reader.push(b"go")
        task = asyncio.create_task(connection.run())
        await until(lambda: writer.written or writer.transport.aborted, "pump ran")
        reader.eof()
        await wait(task)
        return writer.transport.aborted

    assert asyncio.run(scenario(5)) is False
    assert asyncio.run(scenario(4)) is True
