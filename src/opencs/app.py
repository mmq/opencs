"""Wiring and shutdown (DESIGN §3): read config, build the services, start the listener, stop
cleanly on a signal.
"""

import argparse
import asyncio
import logging
import os
import random
import signal
import sqlite3
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from opencs import __version__, policy
from opencs.admin import AdminServer, resolve_token
from opencs.cities import CityManager
from opencs.clock import Clock, MonotonicClock
from opencs.config import AppConfig, ConfigError, ConfigFile, load_config
from opencs.connlog import ConnectionContext, ConnectionLog
from opencs.control import SHUTDOWN_NOTICE, Control, remove_everyone
from opencs.lobby.identity import Identity
from opencs.lobby.lobby import Lobby
from opencs.logsetup import configure_logging
from opencs.net.capture import CaptureFactory
from opencs.net.connection import ConnectionIO, SessionFactory
from opencs.net.listener import ConnectionLimits, Listener
from opencs.routing import Router
from opencs.session.held import HeldGames
from opencs.session.loginlimits import LoginLimits
from opencs.session.persistence import Persistence
from opencs.session.phases import Services
from opencs.session.session import Session
from opencs.store.base import Stores
from opencs.store.memory import memory_stores
from opencs.store.sqlite import SqliteStore, open_stores
from opencs.world.city import City

log = logging.getLogger("opencs.app")


class StartupError(Exception):
    """A startup step failed; the message says which one and why, ready to print (exit code 1)."""


DEFAULT_CONFIG_PATH = Path("config") / "server.toml"


class ThreadHasher:
    """Password hashes on ``HASH_WORKERS`` threads (``session.phases.Hasher``), so a login never
    stalls the event loop. ``hashlib``'s PBKDF2 releases the GIL while it runs."""

    def __init__(self, workers: int = policy.HASH_WORKERS) -> None:
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="opencs-hash")

    def submit(self, work: Callable[[], bytes], done: Callable[[bytes], None]) -> None:
        future = asyncio.get_running_loop().run_in_executor(self._executor, work)

        def finished(f: asyncio.Future[bytes]) -> None:  # on the loop
            if f.cancelled():
                return
            if (exc := f.exception()) is not None:
                log.error("a password hash failed", exc_info=exc)
                return  # the login times out
            done(f.result())

        future.add_done_callback(finished)

    def close(self) -> None:
        """Drop what is waiting; a hash already running finishes on its own."""
        self._executor.shutdown(wait=False, cancel_futures=True)


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
    limits = config.server.limits
    identity = Identity(
        stores.accounts,
        guest_names=config.server.guest_names,
        registration=config.server.registration,
        min_password_length=limits.min_password_length,
        max_accounts=limits.max_accounts,
    )
    rng = rng or random.Random()
    held = HeldGames(clock)
    lobby = Lobby(config.cities, config.server.default_city, rng, held)
    cities = {c.letter: City(c, rng, held) for c in config.cities if c.enabled}
    return Services(
        identity,
        lobby,
        clock,
        cities,
        Router(),
        Persistence(stores.progress, clock),
        held,
        LoginLimits(
            clock,
            max_guests=limits.max_guests,
            max_guests_per_address=limits.max_guests_per_address,
        ),
        hasher=ThreadHasher(),
    )


def build_session_factory(services: Services) -> SessionFactory:
    """The factory the listener calls for each accepted connection."""

    def factory(io: ConnectionIO, context: ConnectionContext) -> Session:
        return Session(io, services, ConnectionLog(logging.getLogger("opencs.session"), context))

    return factory


@dataclass(frozen=True, slots=True)
class Seams:
    """What tests substitute for the real thing. ``stores`` replaces the SQLite file, ``clock`` and
    ``rng`` the real ones; ``services`` is built by the caller so it can look inside (it owns its
    stores, so ``stores`` and ``rng`` are then unused). ``tick_sleep`` paces the city and session
    tickers, which a test pulses by hand instead of waiting; ``shutdown_deadline`` is how long a
    shutdown waits for connections before aborting them."""

    stores: Stores | None = None
    clock: Clock | None = None
    rng: random.Random | None = None
    services: Services | None = None
    tick_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    shutdown_deadline: float = policy.SHUTDOWN_DEADLINE
    #: The environment the admin token is looked up in (``os.environ`` when None).
    environ: Mapping[str, str] | None = None
    #: Receives the admin channel's bound port once it listens.
    admin_ready: Callable[[int], None] | None = None
    #: Paces the countdown of ``shutdown <seconds>``.
    delay: Callable[[float], Awaitable[None]] = asyncio.sleep


async def serve(
    config: AppConfig,
    *,
    stop: asyncio.Event,
    ready: Callable[[int], None] | None = None,
    config_file: ConfigFile | None = None,
    seams: Seams = Seams(),  # noqa: B008  (frozen, so one shared default is safe)
) -> None:
    """Listen on the configured address until ``stop`` is set, then shut down gracefully: every
    player is told, taken out of its game and saved, and the connections are closed within the
    shutdown deadline (DESIGN §6).

    ``ready`` receives the first bound socket's port once listening (port 0 binds an ephemeral
    one). A host naming several addresses binds several sockets; each is logged. Accounts and
    progress go to the SQLite file in ``data_dir`` unless ``seams`` says otherwise.
    """
    services = seams.services
    clock = seams.clock or (services.clock if services else MonotonicClock())
    database: SqliteStore | None = None
    if services is None:
        stores = seams.stores
        if stores is None:  # the real thing: accounts and progress in data_dir (DESIGN §2)
            try:
                stores, database = open_stores(config.server.data_dir)
            except (OSError, sqlite3.Error) as exc:
                raise StartupError(
                    f"cannot open the database in {config.server.data_dir}: "
                    f"{getattr(exc, 'strerror', None) or exc}"
                ) from exc
            log.info("database %s", config.server.data_dir / policy.DATABASE_FILE_NAME)
        services = build_services(config, stores=stores, clock=clock, rng=seams.rng)
    try:
        await _listen(config, services, clock, stop, ready, config_file, seams)
    finally:
        # Connections are closed by now, and each saved its player as it left; this saves anyone
        # still seated, and whatever a failed save left waiting for a retry.
        services.persistence.save_all(services.cities.values())
        if database is not None:
            database.close()
    log.info("stopped")


def _wall_stamp() -> str:
    """The wall-clock start of a capture, for its file name: ``YYYY-MM-DD-HHMMSS``."""
    return datetime.now().strftime("%Y-%m-%d-%H%M%S")


def _capture_factory(config: AppConfig) -> CaptureFactory | None:
    """The wire capture the config asks for, or None (DESIGN §6)."""
    capture = config.server.capture
    if not capture.enabled:
        return None
    return CaptureFactory(
        capture.directory, frozenset(capture.callsigns), capture.max_bytes, _wall_stamp
    )


async def _shutdown(
    services: Services,
    listener: Listener,
    cities: CityManager,
    deadline: float,
) -> None:
    """Stop accepting, end every session with the notice (a game with a system line and the
    unprompted ``'C'``, the Chamber with a line; each player is saved as it leaves), save whoever
    is still seated, stop the tickers, and close the connections, aborting any that outlast
    ``deadline`` (DESIGN §6)."""
    log.info("shutting down")
    listener.stop_accepting()
    remove_everyone(services, SHUTDOWN_NOTICE)
    services.persistence.save_all(services.cities.values())
    await cities.stop()
    await listener.stop(deadline)


def _on_sighup(cities: CityManager, config_file: ConfigFile | None) -> Callable[[], None]:
    """Reload the city configuration on SIGHUP, where the platform has it. Returns the function
    that takes the handler away again."""
    if config_file is None or not hasattr(signal, "SIGHUP"):
        return lambda: None
    loop = asyncio.get_running_loop()

    def reload() -> None:
        log.info("SIGHUP: reloading the city configuration")
        cities.reload_file(config_file.path, config_file.server)  # logs its own outcome

    def remove() -> None:
        loop.remove_signal_handler(signal.SIGHUP)

    loop.add_signal_handler(signal.SIGHUP, reload)
    return remove


def prune_accounts(
    services: Services, now: float, days: int = policy.UNPLAYED_ACCOUNT_DAYS
) -> list[str]:
    """Delete the accounts left unplayed for ``days`` (wall-clock ``now``)."""
    before = now - days * 24 * 3600
    gone = services.identity.prune_unplayed(
        before, lambda key: services.persistence.record(key) is not None
    )
    if gone:
        log.info("removed %d accounts never played in %d days", len(gone), days)
    return gone


async def _prune_accounts_daily(services: Services, days: int) -> None:
    """``prune_accounts`` now and every ``ACCOUNT_PRUNE_INTERVAL``; a failure waits for the next."""
    while True:
        try:
            prune_accounts(services, time.time(), days)
        except Exception:
            log.exception("could not prune unplayed accounts")
        await asyncio.sleep(policy.ACCOUNT_PRUNE_INTERVAL)


async def _listen(
    config: AppConfig,
    services: Services,
    clock: Clock,
    stop: asyncio.Event,
    ready: Callable[[int], None] | None,
    config_file: ConfigFile | None,
    seams: Seams,
) -> None:
    """Listen until ``stop`` is set, ticking the cities, then shut down gracefully."""
    token = (
        None
        if config.server.admin.port is None
        else resolve_token(
            config.server.admin, os.environ if seams.environ is None else seams.environ
        )
    )  # before anything starts: a channel without a token must stop the server, not run open
    limits = config.server.limits
    listener = Listener(
        build_session_factory(services),
        clock,
        limits=ConnectionLimits(
            total=limits.max_connections,
            per_address=limits.max_connections_per_address,
            per_subnet=limits.max_connections_per_subnet,
            unauthenticated=limits.max_unauthenticated,
            unauthenticated_per_address=limits.max_unauthenticated_per_address,
        ),
        sleep=seams.tick_sleep,
        capture=_capture_factory(config),
    )
    try:
        server = await listener.start(config.server.host, config.server.port)
    except OSError as exc:
        raise StartupError(
            f"cannot listen on {config.server.host}:{config.server.port}: {exc.strerror or exc}"
        ) from exc
    cities = CityManager(services, clock, seams.rng or random.Random(), seams.tick_sleep)
    cities.start()
    control = Control(services, cities, stop, config_file, delay=seams.delay)
    admin: AdminServer | None = None
    reload_signal = _on_sighup(cities, config_file)
    pruning = asyncio.create_task(
        _prune_accounts_daily(services, config.server.limits.unplayed_account_days)
    )
    try:
        if token is not None and config.server.admin.port is not None:
            admin = AdminServer(control, token, clock)
            try:
                admin_port = await admin.start(config.server.admin.port)
            except OSError as exc:
                raise StartupError(
                    f"cannot listen for the admin channel on {policy.ADMIN_HOST}:"
                    f"{config.server.admin.port}: {exc.strerror or exc}"
                ) from exc
            if seams.admin_ready is not None:
                seams.admin_ready(admin_port)
        bound = [sock.getsockname()[:2] for sock in server.sockets]
        for address, bound_port in bound:
            log.info("listening on %s:%d", address, bound_port)
        if ready is not None:
            ready(bound[0][1])
        await stop.wait()
    finally:
        pruning.cancel()
        reload_signal()
        control.close()
        if admin is not None:
            await admin.stop()
        await _shutdown(services, listener, cities, seams.shutdown_deadline)
        if isinstance(services.hasher, ThreadHasher):
            services.hasher.close()


async def _run(config: AppConfig, config_file: ConfigFile) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await serve(config, stop=stop, config_file=config_file)


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
    parser.add_argument("--version", action="version", version=f"opencs {__version__}")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="server.toml path")
    parser.add_argument("--port", type=_port, help="override the configured port (0 = ephemeral)")
    parser.add_argument("--host", help="override the configured host")
    parser.add_argument(
        "--data-dir", type=Path, help="override the configured data directory (the SQLite file)"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the server until SIGINT or SIGTERM; return the process exit code."""
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"opencs: {exc}", file=sys.stderr)
        return 2
    config_file = ConfigFile(args.config, config.server)
    overrides = {
        k: v
        for k, v in (("host", args.host), ("port", args.port), ("data_dir", args.data_dir))
        if v is not None
    }
    config = replace(config, server=replace(config.server, **overrides))
    try:
        configure_logging(config.server.logging)
    except OSError as exc:
        print(f"opencs: cannot open the log file: {exc.strerror or exc}", file=sys.stderr)
        return 2
    try:
        asyncio.run(_run(config, config_file))
    except ConfigError as exc:
        print(f"opencs: {exc}", file=sys.stderr)
        return 2
    except StartupError as exc:
        print(f"opencs: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"opencs: cannot start: {exc.strerror or exc}", file=sys.stderr)
        return 1
    return 0
