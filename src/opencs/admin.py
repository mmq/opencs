"""The admin channel: a line-oriented TCP port for the operator (DESIGN §6, M12).

It sits beside ``net/`` because it is I/O, and uses the services through ``Control``; it is never
reachable from a game connection. It listens on 127.0.0.1 only (``policy.ADMIN_HOST``, not
configurable) and refuses a peer that is not loopback. A connection must send the token as its
first line, compared in constant time, within ``ADMIN_AUTH_TIMEOUT``; token attempts are metered
across all connections. After that each line is a command (``help`` lists them); the reply is its
lines followed by an empty line. Input is bounded like any other: a line over ``ADMIN_MAX_LINE``
bytes, a command rate over ``ADMIN_COMMAND_LIMIT`` or a silence over ``ADMIN_IDLE_TIMEOUT`` closes
the connection, and so do more than ``ADMIN_MAX_CONNECTIONS`` at once. The token is never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import ipaddress
import logging
from collections.abc import Mapping

from opencs import policy
from opencs.clock import Clock
from opencs.config import AdminConfig, ConfigError
from opencs.control import Control
from opencs.session.ratelimit import TokenBucket

log = logging.getLogger("opencs.admin")

GREETING = b"opencs admin: send the token\n"


def resolve_token(admin: AdminConfig, environ: Mapping[str, str]) -> str:
    """The admin token: the literal in ``server.toml``, else the environment variable it names.
    Raises ``ConfigError`` if there is none or it is too short: the channel never starts
    without a token."""
    token = admin.token if admin.token is not None else environ.get(admin.token_env, "")
    if not token:
        raise ConfigError(
            f"[admin] port is set but there is no token: set `token` or the "
            f"{admin.token_env} environment variable"
        )
    if len(token) < policy.ADMIN_TOKEN_MIN_LENGTH:
        raise ConfigError(
            f"the admin token must be at least {policy.ADMIN_TOKEN_MIN_LENGTH} characters"
        )
    return token


def _digest(text: bytes) -> bytes:
    return hashlib.sha256(text).digest()


class AdminServer:
    """The listener and its connections."""

    def __init__(
        self,
        control: Control,
        token: str,
        clock: Clock,
        *,
        auth_timeout: float = policy.ADMIN_AUTH_TIMEOUT,
        idle_timeout: float = policy.ADMIN_IDLE_TIMEOUT,
        max_connections: int = policy.ADMIN_MAX_CONNECTIONS,
    ) -> None:
        self._control = control
        self._token = _digest(token.encode("utf-8"))
        self._clock = clock
        self._auth_timeout = auth_timeout
        self._idle_timeout = idle_timeout
        self._max = max_connections
        self._auth_attempts = TokenBucket(*policy.ADMIN_AUTH_LIMIT, clock.now())
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self, port: int) -> int:
        """Listen on the loopback address; returns the bound port (0 asks for a free one)."""
        self._server = await asyncio.start_server(
            self._handle, policy.ADMIN_HOST, port, limit=policy.ADMIN_MAX_LINE
        )
        bound: int = self._server.sockets[0].getsockname()[1]
        log.info("admin channel on %s:%d", policy.ADMIN_HOST, bound)
        return bound

    @property
    def address(self) -> tuple[str, int]:
        """The host and port bound (the host is always loopback)."""
        assert self._server is not None
        host, port = self._server.sockets[0].getsockname()[:2]
        return str(host), int(port)

    async def stop(self) -> None:
        """Stop listening and close every admin connection."""
        if self._server is not None:
            self._server.close()
        for writer in list(self._writers):
            writer.close()
        if self._server is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._server.wait_closed(), policy.SHUTDOWN_ABORT_GRACE)

    # --- one connection ----------------------------------------------------------------------

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        host = str(peer[0]) if peer else "?"
        if len(self._writers) >= self._max or not _is_loopback(host):
            log.warning("admin connection from %s refused", host)
            writer.close()
            return
        self._writers.add(writer)
        try:
            await self._session(reader, writer, host)
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            log.info("admin connection from %s ended", host)
        except Exception:
            log.exception("admin connection from %s failed", host)
        finally:
            self._writers.discard(writer)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _session(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str
    ) -> None:
        writer.write(GREETING)
        if not await self._authenticate(reader, writer, host):
            return
        commands = TokenBucket(*policy.ADMIN_COMMAND_LIMIT, self._clock.now())
        while True:
            try:
                line = await self._read_line(reader, self._idle_timeout)
            except TimeoutError:
                await self._say(writer, ["idle too long: goodbye"])
                return
            if line is None:
                return
            if line.strip().lower() in ("quit", "exit"):
                await self._say(writer, ["goodbye"])
                return
            if not commands.take(self._clock.now()):
                log.warning("admin connection from %s over its command rate; closing", host)
                await self._say(writer, ["too many commands: closing"])
                return
            log.info("admin command from %s: %s", host, line.split(" ", 1)[0].lower())
            try:
                reply = self._control.execute(line)
            except Exception:
                log.exception("admin command %r failed", line.split(" ", 1)[0])
                reply = ["error: the command failed (see the log)"]
            await self._say(writer, reply)

    async def _authenticate(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str
    ) -> bool:
        if not self._auth_attempts.take(self._clock.now()):
            log.warning("admin token attempt from %s refused: too many attempts", host)
            await self._say(writer, ["too many attempts: try later"])
            return False
        try:
            line = await self._read_line(reader, self._auth_timeout)
        except TimeoutError:
            log.warning("admin connection from %s sent no token in time", host)
            return False
        if line is None or not hmac.compare_digest(_digest(line.encode("latin-1")), self._token):
            log.warning("admin connection from %s sent a wrong token", host)
            await self._say(writer, ["denied"])
            return False
        log.info("admin connection from %s authenticated", host)
        await self._say(writer, ["ok"])
        return True

    @staticmethod
    async def _read_line(reader: asyncio.StreamReader, timeout: float) -> str | None:
        """One line without its terminator, None at end of stream. A line over the limit raises
        ``LimitOverrunError`` (the connection is closed); silence raises ``TimeoutError``."""
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\n"), timeout)
        except asyncio.IncompleteReadError:
            return None
        return raw.rstrip(b"\r\n").decode("latin-1")

    @staticmethod
    async def _say(writer: asyncio.StreamWriter, lines: list[str]) -> None:
        text = "".join(f"{_printable(line)}\n" for line in lines) + "\n"
        writer.write(text.encode("ascii"))
        with contextlib.suppress(ConnectionError):
            await writer.drain()


def _printable(line: str) -> str:
    """A reply line that cannot carry a control character to the operator's terminal (a callsign
    or a message from a player appears in some replies)."""
    return "".join(ch if " " <= ch <= "~" else "?" for ch in line)


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
