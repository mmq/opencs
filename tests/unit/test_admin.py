"""The admin channel over real loopback sockets (M12): token, bounds, commands, never logged."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from fixtures.loopback import TIMEOUT, TickGate, connect, enter, frame, local_config, start
from fixtures.pods import report
from fixtures.sessions import World, make_world
from opencs import policy
from opencs.admin import GREETING, AdminServer, _is_loopback, _printable, resolve_token
from opencs.app import Seams, serve
from opencs.cities import CityManager
from opencs.clock import ManualClock
from opencs.config import AdminConfig, ConfigError
from opencs.control import Control
from opencs.messages import server
from opencs.store.memory import memory_stores

TOKEN = "correct horse battery staple"


class AdminClient:
    """A scripted operator: lines out, replies (up to the blank line) in."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer

    async def line(self) -> str:
        raw = await asyncio.wait_for(self.reader.readline(), TIMEOUT)
        return raw.decode("ascii").rstrip("\n")

    async def reply(self) -> list[str]:
        """The lines of one reply: up to the empty line that ends it."""
        lines = []
        while text := await self.line():
            lines.append(text)
        return lines

    async def say(self, text: str | bytes) -> None:
        self.writer.write(text.encode("latin-1") if isinstance(text, str) else text)
        await asyncio.wait_for(self.writer.drain(), TIMEOUT)

    async def command(self, text: str) -> list[str]:
        await self.say(text + "\n")
        return await self.reply()

    async def closed(self) -> bool:
        """True once the server has closed the connection (end of stream)."""
        try:
            return await asyncio.wait_for(self.reader.read(), TIMEOUT) == b""
        except ConnectionResetError:  # closed with our last line still unread: a reset is a close
            return True


async def dial(port: int) -> AdminClient:
    reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), TIMEOUT)
    return AdminClient(reader, writer)


async def login(port: int, token: str = TOKEN) -> AdminClient:
    c = await dial(port)
    assert await c.line() == GREETING.decode().rstrip("\n")
    await c.say(token + "\n")
    assert await c.reply() == ["ok"]
    return c


def control(world: World, stop: asyncio.Event) -> Control:
    manager = CityManager(world.services, world.clock, random.Random(1))
    return Control(world.services, manager, stop, None)


@asynccontextmanager
async def running(
    clock: ManualClock | None = None, **kwargs: float
) -> AsyncIterator[tuple[AdminServer, int, asyncio.Event]]:
    world = make_world()
    stop = asyncio.Event()
    admin = AdminServer(control(world, stop), TOKEN, clock or ManualClock(), **kwargs)  # type: ignore[arg-type]
    port = await admin.start(0)
    try:
        yield admin, port, stop
    finally:
        await admin.stop()


def run(coro: object) -> None:
    asyncio.run(asyncio.wait_for(coro, 4 * TIMEOUT))  # type: ignore[arg-type]


# --- the token ---------------------------------------------------------------------------------


def test_the_right_token_opens_a_command_session() -> None:
    async def scenario() -> None:
        async with running() as (_, port, _):
            c = await login(port)
            assert "status" in "\n".join(await c.command("help"))
            assert (await c.command("status"))[0].startswith("uptime:")
            assert await c.command("nonsense") == ["unknown command 'nonsense': try help"]
            await c.say("quit\n")
            assert await c.reply() == ["goodbye"] and await c.closed()

    run(scenario())


def test_a_wrong_token_is_denied_and_closed() -> None:
    async def scenario() -> None:
        async with running() as (_, port, stop):
            c = await dial(port)
            await c.line()
            await c.say("shutdown\n")  # a command in place of the token is a wrong token
            assert await c.reply() == ["denied"]
            assert await c.closed() and not stop.is_set()
            c = await dial(port)
            await c.line()
            await c.say(TOKEN + "x\n")  # near misses on both sides of the right one
            assert await c.reply() == ["denied"]
            c = await dial(port)
            await c.line()
            await c.say(TOKEN[:-1] + "\n")
            assert await c.reply() == ["denied"]

    run(scenario())


def test_token_attempts_are_metered_across_connections() -> None:
    async def scenario() -> None:
        clock = ManualClock()
        burst, per_second = policy.ADMIN_AUTH_LIMIT
        async with running(clock) as (_, port, _):
            for _ in range(burst):  # the last attempt the burst allows is still judged
                c = await dial(port)
                await c.line()
                await c.say("wrong token\n")
                assert await c.reply() == ["denied"]
            c = await dial(port)  # the first one past it is not even looked at
            await c.line()
            await c.say(TOKEN + "\n")
            assert await c.reply() == ["too many attempts: try later"]
            assert await c.closed()
            clock.advance(1 / per_second)  # one token has come back
            await login(port)

    run(scenario())


def test_no_token_in_time_closes_without_a_reply() -> None:
    async def scenario() -> None:
        async with running(auth_timeout=0.0) as (_, port, _):
            c = await dial(port)
            await c.line()  # the greeting
            assert await c.closed()

    run(scenario())


def test_a_silent_operator_is_dropped_after_the_idle_timeout() -> None:
    async def scenario() -> None:
        async with running(idle_timeout=0.0) as (_, port, _):
            c = await login(port)
            assert await c.reply() == ["idle too long: goodbye"] and await c.closed()

    run(scenario())


# --- bounds ------------------------------------------------------------------------------------


def test_an_overlong_line_closes_before_and_after_the_token() -> None:
    async def scenario() -> None:
        async with running() as (_, port, _):
            c = await dial(port)
            await c.line()
            await c.say(b"x" * (policy.ADMIN_MAX_LINE + 1))
            assert await c.closed()
            c = await login(port)
            await c.say(b"y" * (policy.ADMIN_MAX_LINE + 1))
            assert await c.closed()

    run(scenario())


def test_the_longest_allowed_line_is_still_a_command() -> None:
    async def scenario() -> None:
        async with running() as (_, port, _):
            c = await login(port)
            line = "announce " + "z" * (policy.ADMIN_MAX_LINE - len("announce ") - 1)
            assert len(line) + 1 == policy.ADMIN_MAX_LINE
            assert await c.command(line) == ["announced to 0"]

    run(scenario())


def test_commands_over_the_rate_close_the_connection() -> None:
    async def scenario() -> None:
        clock = ManualClock()
        async with running(clock) as (_, port, _):
            c = await login(port)
            burst = policy.ADMIN_COMMAND_LIMIT[0]
            for _ in range(burst):
                assert await c.command("help")
            await c.say("help\n")
            assert await c.reply() == ["too many commands: closing"] and await c.closed()

    run(scenario())


def test_more_connections_than_the_limit_are_closed_at_once() -> None:
    async def scenario() -> None:
        async with running(max_connections=1) as (_, port, _):
            first = await login(port)
            second = await dial(port)
            assert await second.closed()  # not even a greeting
            assert await first.command("help")  # the first is unaffected

    run(scenario())


# --- where it listens and who may ---------------------------------------------------------------


def test_it_listens_on_loopback_only() -> None:
    async def scenario() -> None:
        async with running() as (admin, port, _):
            assert admin.address == ("127.0.0.1", port)

    run(scenario())


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("127.0.0.2", True),
        ("::1", True),
        ("10.0.0.5", False),
        ("192.168.1.2", False),
        ("0.0.0.0", False),
        ("8.8.8.8", False),
        ("::", False),
        ("?", False),
        ("", False),
    ],
)
def test_only_a_loopback_peer_is_served(host: str, expected: bool) -> None:
    assert _is_loopback(host) is expected


def test_replies_cannot_carry_control_characters() -> None:
    assert _printable("A\x1b[2JB\x07ü") == "A?[2JB??"


# --- the token in config and environment ---------------------------------------------------------


def test_the_token_comes_from_the_config_or_the_environment() -> None:
    assert resolve_token(AdminConfig(1, "x" * 16), {}) == "x" * 16
    env = {"OPENCS_ADMIN_TOKEN": "y" * 16}
    assert resolve_token(AdminConfig(1), env) == "y" * 16
    assert resolve_token(AdminConfig(1, token_env="OTHER"), {"OTHER": "z" * 20}) == "z" * 20
    assert resolve_token(AdminConfig(1, "x" * 16), env) == "x" * 16  # the file's wins


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({}, "no token"),
        ({"OPENCS_ADMIN_TOKEN": ""}, "no token"),
        ({"OPENCS_ADMIN_TOKEN": "x" * (policy.ADMIN_TOKEN_MIN_LENGTH - 1)}, "at least"),
    ],
)
def test_no_usable_token_means_the_channel_does_not_start(
    env: dict[str, str], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        resolve_token(AdminConfig(1), env)


def test_a_token_of_exactly_the_minimum_length_is_accepted() -> None:
    token = "x" * policy.ADMIN_TOKEN_MIN_LENGTH
    assert resolve_token(AdminConfig(1), {"OPENCS_ADMIN_TOKEN": token}) == token


def test_the_token_never_reaches_a_log_record() -> None:
    class Collect(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.text: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.text.append(f"{record.getMessage()} {record.args!r}")

    async def scenario() -> list[str]:
        collect = Collect()
        root = logging.getLogger()
        previous = root.level
        root.addHandler(collect)
        root.setLevel(logging.DEBUG)
        try:
            async with running() as (_, port, _):
                bad = await dial(port)
                await bad.line()
                await bad.say("not-the-token-at-all\n")
                await bad.reply()
                c = await login(port)
                await c.command("help")
        finally:
            root.removeHandler(collect)
            root.setLevel(previous)
        return collect.text

    joined = "\n".join(asyncio.run(asyncio.wait_for(scenario(), 4 * TIMEOUT)))
    assert "authenticated" in joined and "wrong token" in joined  # the events were logged
    assert TOKEN not in joined and "not-the-token-at-all" not in joined


# --- the whole server ----------------------------------------------------------------------------


def test_the_operator_announces_kicks_and_shuts_down_a_running_server() -> None:
    async def scenario() -> None:
        config = local_config()
        config = replace(config, server=replace(config.server, admin=AdminConfig(port=0)))
        admin_port: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        gate = TickGate()
        seams = Seams(
            clock=ManualClock(1000.0),
            stores=memory_stores(),
            tick_sleep=gate.sleep,
            environ={policy.ADMIN_TOKEN_ENV: TOKEN},
            admin_ready=admin_port.set_result,
        )
        stop, task, port = await start(config, seams=seams)
        player, _ = await enter(port, "RED")
        await player.send(frame(report(4)))
        await player.expect_frame("A")
        other = await connect(port)  # sits at the logon: counted, untouched by the announcement

        op = await login(await asyncio.wait_for(admin_port, TIMEOUT))
        status = await op.command("status")
        assert status[1] == "connections: 2 (in game 1, Chamber 0, logon 1)"
        assert "city A CYBERCITY ONE: 1 players (RED 1)" in status
        assert await op.command("announce lights out") == ["announced to 1"]
        assert await player.expect_frame("a") == server.Chat(100, 0, b"lights out")
        assert await op.command("kick joingeni test") == ["kicked JOINGENI (1 connection(s))"]
        assert await player.expect_frame("a") == server.Chat(
            100, 0, b"REMOVED BY AN ADMINISTRATOR: test"
        )
        assert await player.expect_frame("C") == server.QuitAck()
        assert await op.command("shutdown") == ["shutting down now"]
        await asyncio.wait_for(task, TIMEOUT)
        assert await op.closed()
        assert stop.is_set()
        other.writer.close()
        player.writer.close()

    run(scenario())


def test_a_server_whose_admin_port_has_no_token_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An empty environment given to the seam means "no token", whatever the shell holds.
    monkeypatch.setenv(policy.ADMIN_TOKEN_ENV, "x" * 21)

    async def scenario() -> None:
        config = local_config()
        config = replace(config, server=replace(config.server, admin=AdminConfig(port=0)))
        stop = asyncio.Event()
        seams = Seams(environ={}, stores=memory_stores())
        with pytest.raises(ConfigError, match="no token"):
            await serve(config, stop=stop, seams=seams)

    run(scenario())
