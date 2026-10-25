"""A client that never reads fills only its own outbox and is cut; the others are not slowed
(DESIGN §3.3, §5), over real loopback sockets with no sleeping."""

from __future__ import annotations

import asyncio
import socket

from fixtures.loopback import (
    TIMEOUT,
    Client,
    LogWatch,
    TickGate,
    enter,
    frame,
    local_config,
    start,
)
from fixtures.pods import report
from opencs import policy
from opencs.app import build_services
from opencs.clock import ManualClock
from opencs.messages import client, server

#: Marks the last report of the flood: no other report has this x offset.
MARKER_X = 77
#: Reports sent in one go: inside the relay budget (``RELAY_LIMIT_POD_STATE``) and the frame budget.
BATCH = policy.RELAY_LIMIT_POD_STATE[0]
#: Gives up if the flood has not overflowed the stalled outbox after this many reports (the kernel's
#: socket buffers and the transport's high-water mark absorb the first few hundred kilobytes).
MAX_REPORTS = 400_000


async def connect_with_tiny_receive_buffer(port: int) -> Client:
    """A client whose receive window is small, so the server's socket buffer fills soon."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
    sock.setblocking(False)
    await asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", port))
    reader, writer = await asyncio.open_connection(sock=sock)
    return Client(reader, writer)


async def pong(sender: Client) -> None:
    """Read until the reply to the sender's ping: the batch before it has been handled."""
    while await sender.expect_frame("E") != server.PingReply():
        pass


async def observe(watcher: Client, seen_marker: asyncio.Event) -> None:
    """Read the watcher's frames as they come, and note the flood's last one."""
    while True:
        message = await watcher.expect_frame("b")
        if isinstance(message, server.PodState) and message.pos.x_off == MARKER_X:
            seen_marker.set()
            return


def test_a_client_that_never_reads_is_cut_and_the_others_still_get_everything() -> None:
    async def scenario() -> None:
        clock, gate = ManualClock(1000.0), TickGate()
        config = local_config()
        services = build_services(config, clock=clock)
        stop, task, port = await start(
            config, clock=clock, services=services, tick_sleep=gate.sleep
        )
        # Accounts, not guests: all three come from 127.0.0.1 (policy.MAX_GUESTS_PER_ADDRESS).
        stalled, _ = await enter(port, "RED", "STALLED,pw")
        sender, _ = await enter(port, "RED", "SENDER,pw")
        watcher, _ = await enter(port, "RED", "WATCHER,pw")
        for c in (stalled, sender, watcher):
            await c.send(frame(report(4, x_off=75, z_off=75)))
        await sender.expect_frame("A")  # everyone has been introduced and is reporting
        # From here the stalled client reads nothing more. The watcher reads continuously.
        seen_marker = asyncio.Event()
        watching = asyncio.create_task(observe(watcher, seen_marker))
        with LogWatch("opencs.net", "outbox over") as cut:
            sent = 0
            while cut.count == 0 and sent < MAX_REPORTS:
                clock.advance(policy.RELAY_LIMIT_POD_STATE[0])  # refills the budget
                await sender.send(
                    b"".join(frame(report(0, x_off=-40 - n % 5)) for n in range(BATCH))
                )
                await sender.send(frame(client.Ping()))  # answered after the batch is handled
                await pong(sender)
                sent += BATCH
            assert cut.count == 1, f"no overflow after {sent} reports"
        # The sender was never cut, and the watcher still receives the flood's last report.
        clock.advance(policy.RELAY_LIMIT_POD_STATE[0])
        await sender.send(frame(report(0, x_off=MARKER_X)))
        await asyncio.wait_for(seen_marker.wait(), TIMEOUT)
        watching.cancel()
        # The stalled client's connection is gone once it reads again.
        while await asyncio.wait_for(stalled.reader.read(65536), TIMEOUT):
            pass  # what was already in flight, then the end of the stream
        for c in (stalled, sender, watcher):
            c.writer.close()
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)

    asyncio.run(asyncio.wait_for(scenario(), 20 * TIMEOUT))
