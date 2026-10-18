"""The running cities: their tickers, and reloading their configuration (DESIGN §4, §6; M12).

A reload re-reads ``config/cities/*.toml``. A running game is never changed under its players
(II §17), so:

* a **new** city appears at once;
* a **changed** city takes its new configuration the next time its game is empty (at once if it
  already is): until then the lobby keeps offering, and the world keeps running, the old one;
* a city that is **removed or disabled** stops taking ``/PLAY`` at once and disappears when its
  game is empty (a player holding a ``/RESUME`` slot keeps it from being empty).

"Empty" is ``City.in_game`` false: nobody in it and no slot held. Everything here runs on the event
loop between awaits, like the cities themselves, so a reload is atomic.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from opencs import policy
from opencs.clock import Clock
from opencs.config import CityConfig, ConfigError, ServerConfig, load_config
from opencs.routing import Router
from opencs.session.held import HeldGames
from opencs.session.phases import Services
from opencs.world.city import City

log = logging.getLogger("opencs.app")


@dataclass(frozen=True, slots=True)
class ReloadReport:
    """What a reload did, by city letter."""

    added: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()  # took the new configuration now (the game was empty)
    waiting: tuple[str, ...] = ()  # will, when the game is next empty
    closing: tuple[str, ...] = ()  # no longer offered; will go when empty
    removed: tuple[str, ...] = ()  # gone now (they were empty)
    server_changed: bool = False  # server.toml differs; only a restart applies it

    def lines(self) -> list[str]:
        """The report as text for the admin channel and the log."""
        names = (
            ("added", self.added),
            ("changed", self.changed),
            ("waiting for the game to empty", self.waiting),
            ("closed to new players, going when empty", self.closing),
            ("removed", self.removed),
        )
        out = [f"{label}: {', '.join(letters)}" for label, letters in names if letters]
        if self.server_changed:
            out.append("server.toml differs from the running server: restart to apply it")
        return out or ["no city changed"]


class CityManager:
    """Owns the city tickers and applies reloads."""

    def __init__(
        self,
        services: Services,
        clock: Clock,
        rng: random.Random,
        tick_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._services = services
        self._clock = clock
        self._rng = rng
        self._sleep = tick_sleep
        self._tickers: dict[str, asyncio.Task[None]] = {}
        #: Letter -> the configuration to take when that city's game is empty.
        self._pending: dict[str, CityConfig] = {}
        #: Letters no longer offered, waiting to be empty.
        self._closing: set[str] = set()

    # --- tickers -----------------------------------------------------------------------------

    def start(self) -> None:
        for letter in self._services.cities:
            self._start_ticker(letter)

    async def stop(self) -> None:
        """Cancel every ticker and wait for them to end."""
        tickers = list(self._tickers.values())
        self._tickers.clear()
        for ticker in tickers:
            ticker.cancel()
        await asyncio.gather(*tickers, return_exceptions=True)

    def _start_ticker(self, letter: str) -> None:
        city = self._services.cities[letter]
        self._tickers[letter] = asyncio.create_task(
            run_city(
                city,
                self._services.router,
                self._clock,
                self._sleep,
                after_tick=lambda: self._settle(letter, city),
            )
        )

    def _settle(self, letter: str, city: City) -> bool:
        """After a tick of ``city``: apply what was waiting for its game to empty. True ends this
        ticker (the city was replaced or removed)."""
        if self._services.cities.get(letter) is not city:
            return True
        if city.in_game:
            return False
        if letter in self._closing:
            self._remove(letter)
            return True
        pending = self._pending.pop(letter, None)
        if pending is not None:
            self._replace(pending)
            return True
        return False

    # --- reload ------------------------------------------------------------------------------

    def reload(
        self, configs: Sequence[CityConfig], *, server_changed: bool = False
    ) -> ReloadReport:
        """Apply a validated set of city configurations (see the module docstring)."""
        wanted = {c.letter: c for c in configs if c.enabled}
        cities = self._services.cities
        added: list[str] = []
        changed: list[str] = []
        waiting: list[str] = []
        closing: list[str] = []
        removed: list[str] = []
        for letter, config in sorted(wanted.items()):
            existing = cities.get(letter)
            if existing is None:
                self._add(config)
                added.append(letter)
                continue
            if letter in self._closing:  # asked for again before it emptied
                self._closing.discard(letter)
                self._services.lobby.set_city(existing.config)
            if existing.config == config:
                self._pending.pop(letter, None)
            elif existing.in_game:
                self._pending[letter] = config
                waiting.append(letter)
            else:
                self._replace(config)
                changed.append(letter)
        for letter in sorted(set(cities) - set(wanted)):
            self._pending.pop(letter, None)
            self._services.lobby.close_city(letter)
            if cities[letter].in_game:
                self._closing.add(letter)
                closing.append(letter)
            else:
                self._remove(letter)
                removed.append(letter)
        report = ReloadReport(
            tuple(added),
            tuple(changed),
            tuple(waiting),
            tuple(closing),
            tuple(removed),
            server_changed,
        )
        log.info("reload: %s", "; ".join(report.lines()))
        return report

    def _held(self) -> HeldGames:
        return self._services.held

    def _add(self, config: CityConfig) -> None:
        self._services.cities[config.letter] = City(config, self._rng, self._held())
        self._services.lobby.set_city(config)
        self._start_ticker(config.letter)

    def _replace(self, config: CityConfig) -> None:
        """Swap in a fresh city with the new configuration (its game is empty)."""
        letter = config.letter
        self._services.cities[letter] = City(config, self._rng, self._held())
        self._services.lobby.set_city(config)
        self._cancel_ticker(letter)
        self._start_ticker(letter)

    def _remove(self, letter: str) -> None:
        del self._services.cities[letter]
        self._closing.discard(letter)
        self._cancel_ticker(letter)

    def _cancel_ticker(self, letter: str) -> None:
        ticker = self._tickers.pop(letter, None)
        if ticker is not None and ticker is not asyncio.current_task():
            ticker.cancel()  # a ticker settling its own city ends itself (``_settle`` is True)

    # --- from a file -------------------------------------------------------------------------

    def reload_file(self, path: Path, running: ServerConfig) -> list[str]:
        """Read and validate everything under ``path`` first; an invalid file changes nothing and
        the error is the report. Returns the report's lines."""
        try:
            config = load_config(path)
        except ConfigError as exc:
            log.warning("reload refused: %s", exc)
            return [f"reload refused, nothing changed: {exc}"]
        return self.reload(config.cities, server_changed=config.server != running).lines()


async def run_city(
    city: City,
    router: Router,
    clock: Clock,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    after_tick: Callable[[], bool] | None = None,
) -> None:
    """Tick one city ``TICK_HZ`` times a second and deliver what it says (DESIGN §3.3). A bug in
    the city's rules is logged and the next tick runs (DESIGN §5). ``sleep`` is for tests.
    ``after_tick`` runs after each tick and returns True to end the ticker."""
    while True:
        await sleep(1 / policy.TICK_HZ)
        try:
            router.deliver(city, city.tick(clock.now()))
        except Exception:
            log.exception("city %s failed on a tick", city.config.letter)
        if after_tick is not None and after_tick():
            return
