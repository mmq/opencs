"""Wiring and shutdown (DESIGN §3): read config, build the services, start the listener, stop
cleanly on a signal.
"""

import argparse
import asyncio
import logging
import random
import signal
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from pathlib import Path

from opencs import policy
from opencs.clock import Clock, MonotonicClock
from opencs.config import AppConfig, ConfigError, load_config
from opencs.connlog import ConnectionContext, ConnectionLog
from opencs.lobby.identity import Identity
from opencs.lobby.lobby import Lobby
from opencs.net.connection import ConnectionIO, SessionFactory
from opencs.net.listener import Listener
from opencs.routing import Router
from opencs.session.persistence import Persistence
from opencs.session.phases import Services
from opencs.session.session import Session
from opencs.store.base import Stores
from opencs.store.memory import memory_stores
from opencs.store.sqlite import SqliteStore, open_stores
from opencs.world.city import City

log = logging.getLogger("opencs.app")

DEFAULT_CONFIG_PATH = Path("config") / "server.toml"


def build_services(
    config: AppConfig,
    *,
    stores: Stores | None = None,
    clock: Clock | None = None,
    rng: random.Random | None = None,
) -> Services:
    """Build what every session shares: identity, the lobby, one ``City`` per enabled city and the
    router. Without ``stores`` accounts and progress are kept in memory (tests)."""
    clock = clock or MonotonicClock()
    stores = stores or memory_stores()
    identity = Identity(stores.accounts)
    rng = rng or random.Random()
    lobby = Lobby(config.cities, config.server.default_city, rng)
    cities = {c.letter: City(c, rng) for c in config.cities if c.enabled}
    return Services(identity, lobby, clock, cities, Router(), Persistence(stores.progress))


def build_session_factory(services: Services) -> SessionFactory:
    """The factory the listener calls for each accepted connection."""

    def factory(io: ConnectionIO, context: ConnectionContext) -> Session:
        return Session(io, services, ConnectionLog(logging.getLogger("opencs.session"), context))

    return factory


async def run_city(
    city: City,
    router: Router,
    clock: Clock,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Tick one city ``TICK_HZ`` times a second and deliver what it says (DESIGN §3.3). A bug in
    the city's rules is logged and the next tick runs (DESIGN §5). ``sleep`` is for tests."""
    while True:
        await sleep(1 / policy.TICK_HZ)
        try:
            router.deliver(city, city.tick(clock.now()))
        except Exception:
            log.exception("city %s failed on a tick", city.config.letter)


async def serve(
    config: AppConfig,
    *,
    stop: asyncio.Event,
    ready: Callable[[int], None] | None = None,
    stores: Stores | None = None,
    clock: Clock | None = None,
    rng: random.Random | None = None,
    services: Services | None = None,
    tick_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Listen on the configured address until ``stop`` is set, then close every connection.

    ``ready`` receives the first bound socket's port once listening (port 0 binds an ephemeral
    one). A host naming several addresses binds several sockets; each is logged. Accounts and
    progress go to the SQLite file in ``data_dir`` unless ``stores`` says otherwise. ``stores``,
    ``clock`` and ``rng`` are for tests, as are ``services`` (built by the caller, so it can look
    inside, and owns its stores; ``stores`` and ``rng`` are then unused) and ``tick_sleep``, the
    pacing of the city and session tickers (a test pulses it by hand instead of waiting).
    """
    clock = clock or (services.clock if services else MonotonicClock())
    database: SqliteStore | None = None
    if services is None:
        if stores is None:  # the real thing: accounts and progress in data_dir (DESIGN §2)
            stores, database = open_stores(config.server.data_dir)
            log.info("database %s", config.server.data_dir / policy.DATABASE_FILE_NAME)
        services = build_services(config, stores=stores, clock=clock, rng=rng)
    try:
        await _listen(config, services, clock, stop, ready, tick_sleep)
    finally:
        # Connections are closed by now, and each saved its player as it left; this saves anyone
        # still seated (M12 makes shutdown graceful on top of it).
        services.persistence.save_all(services.cities.values())
        if database is not None:
            database.close()
    log.info("stopped")


async def _listen(
    config: AppConfig,
    services: Services,
    clock: Clock,
    stop: asyncio.Event,
    ready: Callable[[int], None] | None,
    tick_sleep: Callable[[float], Awaitable[None]],
) -> None:
    """Listen until ``stop`` is set, ticking the cities, then close every connection."""
    listener = Listener(build_session_factory(services), clock, sleep=tick_sleep)
    server = await listener.start(config.server.host, config.server.port)
    tickers = [
        asyncio.create_task(run_city(city, services.router, clock, tick_sleep))
        for city in services.cities.values()
    ]
    bound = [sock.getsockname()[:2] for sock in server.sockets]
    for address, bound_port in bound:
        log.info("listening on %s:%d", address, bound_port)
    port: int = bound[0][1]
    if ready is not None:
        ready(port)
    try:
        await stop.wait()
    finally:
        for ticker in tickers:
            ticker.cancel()
        await asyncio.gather(*tickers, return_exceptions=True)
        await listener.stop()


async def _run(config: AppConfig) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await serve(config, stop=stop)


def _port(text: str) -> int:
    """argparse type: a TCP port number, validated like the TOML ``port``."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not an integer") from None
    if not policy.MIN_PORT <= value <= policy.MAX_PORT:
        raise argparse.ArgumentTypeError(f"{value} is outside {policy.MIN_PORT}..{policy.MAX_PORT}")
    return value


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="opencs", description="OpenCS game server")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="server.toml path")
    parser.add_argument("--port", type=_port, help="override the configured port (0 = ephemeral)")
    parser.add_argument("--host", help="override the configured host")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the server until SIGINT or SIGTERM; return the process exit code."""
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"opencs: {exc}", file=sys.stderr)
        return 2
    overrides = {k: v for k, v in (("host", args.host), ("port", args.port)) if v is not None}
    config = replace(config, server=replace(config.server, **overrides))
    logging.basicConfig(
        level=config.server.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_run(config))
    except OSError as exc:
        server = config.server
        print(
            f"opencs: cannot listen on {server.host}:{server.port}: {exc.strerror or exc}",
            file=sys.stderr,
        )
        return 1
    return 0
