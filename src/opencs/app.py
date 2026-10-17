"""Wiring and shutdown (DESIGN §3): read config, start the listener, stop cleanly on a signal.

For now an accepted connection is closed at once; the net layer arrives in M3.
"""

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from opencs.config import AppConfig, ConfigError, load_config

log = logging.getLogger("opencs.app")

DEFAULT_CONFIG_PATH = Path("config") / "server.toml"


async def _refuse(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    writer.close()


async def serve(
    config: AppConfig,
    *,
    stop: asyncio.Event,
    ready: Callable[[int], None] | None = None,
) -> None:
    """Listen on the configured address until ``stop`` is set, then close the listener.

    ``ready`` receives the bound port once listening (port 0 binds an ephemeral one).
    """
    server = await asyncio.start_server(_refuse, config.server.host, config.server.port)
    port: int = server.sockets[0].getsockname()[1]
    log.info("listening on %s:%d", config.server.host, port)
    if ready is not None:
        ready(port)
    try:
        await stop.wait()
    finally:
        server.close()
        await server.wait_closed()
    log.info("stopped")


async def _run(config: AppConfig) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await serve(config, stop=stop)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="opencs", description="OpenCS game server")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="server.toml path")
    parser.add_argument("--port", type=int, help="override the configured port (0 = ephemeral)")
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
    asyncio.run(_run(config))
    return 0
