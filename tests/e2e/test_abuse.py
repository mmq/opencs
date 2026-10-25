"""Hostile clients against a real server on loopback (DESIGN §5, docs/robustness.md "Threat model").

Each test plays one abuse a public server meets, through real sockets and the real app, and checks
that the server bounds it and honest players are unaffected. Time is a ``ManualClock``, so a rate
limit refills only when a test says so.
"""

from __future__ import annotations

import asyncio
import socket
import time

from fixtures.loopback import TIMEOUT, Client, LogWatch, TickGate, connect, local_config, start
from opencs import policy
from opencs.app import build_services
from opencs.clock import ManualClock
from opencs.lobby.identity import hash_password
from opencs.store.memory import memory_stores


async def chamber(port: int, account: str, client: Client | None = None) -> Client:
    """A client logged in as ``account`` and in the Chamber, version check answered."""
    c = client or await connect(port)
    await c.send(f"HHH\r{account}\r<Not Telling!>\rM 1380;3\r")
    await c.expect(b"\x05PRESS RETURN\r")
    await c.send("\r")
    await c.expect(b"\x05A\r")
    return c


async def logon(port: int, account: str) -> bytes:
    """One account line on a fresh connection: everything the server sent until it asked for the
    password (accepted) or closed (refused)."""
    c = await connect(port)
    await c.send(f"HHH\r{account}\r")
    seen = b""
    while b"PASSWORD: " not in seen:
        data = await asyncio.wait_for(c.reader.read(4096), TIMEOUT)
        if not data:
            break
        seen += data
    c.writer.close()
    return seen


async def until_closed(c: Client) -> None:
    """Read until the server closes the connection."""
    while await asyncio.wait_for(c.reader.read(65536), TIMEOUT):
        pass


def test_a_chat_flood_spread_over_trips_through_the_star_prompt_is_closed() -> None:
    async def scenario() -> None:
        clock = ManualClock(1000.0)
        stop, task, port = await start(local_config(), clock=clock, tick_sleep=TickGate().sleep)
        c = await chamber(port, "FLOODER,secret")
        cycle = "x\r" * (policy.CHAMBER_BURST_LINES - 1) + "/QUIT\rM 1380;3\r\r"
        await c.send(cycle * 30)  # the clock never moves: no budget comes back
        await until_closed(c)
        c.writer.close()
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)

    asyncio.run(asyncio.wait_for(scenario(), 10 * TIMEOUT))


async def tiny_window(port: int) -> Client:
    """A client whose receive window is small, so the server's socket buffer fills soon."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
    sock.setblocking(False)
    await asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", port))
    reader, writer = await asyncio.open_connection(sock=sock)
    return Client(reader, writer)


def test_chat_never_overflows_a_player_who_reads_slowly() -> None:
    async def scenario() -> tuple[int, int, bool]:
        clock = ManualClock(1000.0)
        config = local_config()
        services = build_services(config, stores=memory_stores(), clock=clock)
        # No session ticks: the victim's idle timer must not end the test for it.
        stop, task, port = await start(
            config, clock=clock, services=services, tick_sleep=TickGate().sleep
        )
        victim = await chamber(port, "VICTIM,secret", await tiny_window(port))  # reads no more
        sender = await chamber(port, "SENDER,secret")
        watcher = await chamber(port, "WATCHER,secret")
        with LogWatch("opencs.net", "outbox over") as cut:
            batch = 0
            while services.lobby.chat_dropped < policy.OUTBOX_LIMIT and batch < 5000:
                clock.advance(10.0)  # both chat budgets full again
                lines = [f"chat {batch} {n}" for n in range(policy.CHAMBER_BURST_LINES)]
                await sender.send("".join(f"{line}\r" for line in lines))
                await watcher.expect(lines[-1].encode())  # the batch has been handled
                batch += 1
            cuts = cut.count
        alive = any(
            s.pilot is not None and s.pilot.callsign == "VICTIM" for s in services.sessions.all()
        )
        for c in (victim, sender, watcher):
            c.writer.close()
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)
        return services.lobby.chat_dropped, cuts, alive

    dropped, cuts, alive = asyncio.run(asyncio.wait_for(scenario(), 20 * TIMEOUT))
    assert dropped >= policy.OUTBOX_LIMIT  # more than the outbox holds was sent past the victim
    assert cuts == 0  # and nobody was cut for it
    assert alive


def test_a_guess_storm_on_one_account_locks_only_it_and_never_stalls_the_loop() -> None:
    async def scenario() -> tuple[list[bytes], float]:
        clock = ManualClock(1000.0)
        config = local_config()
        services = build_services(config, stores=memory_stores(), clock=clock)
        victim = services.identity.login("VICTIM,rightpass")  # registered before the storm
        services.identity.release(victim)  # type: ignore[arg-type]
        stop, task, port = await start(config, clock=clock, services=services)
        gaps: list[float] = []
        beating = True

        async def heartbeat() -> None:
            last = time.perf_counter()
            while beating:
                await asyncio.sleep(0.001)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now

        beat = asyncio.create_task(heartbeat())
        burst = policy.FAILED_LOGINS_PER_ACCOUNT[0]
        outs = [await logon(port, "VICTIM,wrongpass") for _ in range(burst)]
        beating = False
        await beat
        outs.append(await logon(port, "VICTIM,wrongpass"))  # locked: refused without a check
        outs.append(await logon(port, "VICTIM,rightpass"))  # even for the owner, for now
        outs.append(await logon(port, "OTHER,otherpass"))  # another account is not affected
        clock.advance(1 / policy.FAILED_LOGINS_PER_ACCOUNT[1])
        outs.append(await logon(port, "VICTIM,rightpass"))  # minutes later, the owner gets in
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)
        return outs, max(gaps)

    outs, worst_gap = asyncio.run(asyncio.wait_for(scenario(), 10 * TIMEOUT))
    burst = policy.FAILED_LOGINS_PER_ACCOUNT[0]
    assert all(b"WRONG PASSWORD" in o for o in outs[:burst])
    assert b"TOO MANY WRONG PASSWORDS" in outs[burst]
    assert b"TOO MANY WRONG PASSWORDS" in outs[burst + 1]
    assert b"PASSWORD: " in outs[burst + 2]
    assert b"PASSWORD: " in outs[burst + 3]
    began = time.perf_counter()
    hash_password("x", b"0" * 16, policy.PBKDF2_ITERATIONS)
    one_hash = time.perf_counter() - began
    assert worst_gap < one_hash  # the hashes ran beside the loop, not on it


def test_a_reconnect_loop_is_refused_and_logged_once() -> None:
    async def scenario() -> tuple[int, int, int, bytes]:
        clock = ManualClock(1000.0)
        stop, task, port = await start(local_config(), clock=clock, tick_sleep=TickGate().sleep)
        accepted = refused = 0
        with LogWatch("opencs.net", "refusing 127.0.0.1") as lines:
            for _ in range(40):
                c = await connect(port)
                try:
                    await c.send("HHH")
                    answered = bool(await asyncio.wait_for(c.reader.read(64), TIMEOUT))
                except ConnectionResetError:  # closed at once: the reset can beat our bytes
                    answered = False
                accepted += answered
                refused += not answered
                c.writer.close()
            logged = lines.count
        clock.advance(policy.REFUSAL_LOG_INTERVAL)  # the address's rate has refilled
        again = await connect(port)
        await again.send("HHH")
        reply = await asyncio.wait_for(again.reader.read(64), TIMEOUT)
        again.writer.close()
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)
        return accepted, refused, logged, reply

    accepted, refused, logged, reply = asyncio.run(asyncio.wait_for(scenario(), 10 * TIMEOUT))
    assert accepted <= policy.CONNECT_RATE_PER_ADDRESS[0]
    assert refused == 40 - accepted
    assert logged == 1  # one line for the whole loop
    assert reply.startswith(b"CONNECT")


def test_guests_from_one_address_are_capped_and_an_account_still_gets_in() -> None:
    async def scenario() -> list[bytes]:
        stop, task, port = await start(local_config(), clock=ManualClock(1000.0))
        guests = [await chamber(port, "JOINGENIE") for _ in range(policy.MAX_GUESTS_PER_ADDRESS)]
        extra = await logon(port, "JOINGENIE")
        member = await logon(port, "MEMBER,secret")
        for g in guests:
            g.writer.close()
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)
        return [extra, member]

    extra, member = asyncio.run(asyncio.wait_for(scenario(), 10 * TIMEOUT))
    assert b"TOO MANY GUESTS ONLINE" in extra
    assert b"PASSWORD: " in member
