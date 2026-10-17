"""Typed, validated configuration read from TOML (DESIGN §2, §4).

``load_config`` turns ``server.toml`` and the per-city files into frozen dataclasses. Unknown keys
and wrong types raise ``ConfigError`` naming the file and the key, so a typo fails at startup.
"""

import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opencs import policy

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(Exception):
    """A configuration file is unreadable or holds a bad value."""


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Process-wide settings (``server.toml``)."""

    host: str
    port: int
    data_dir: Path
    cities_dir: Path
    log_level: str
    default_city: str  # II §4.2: the city a bare /PLAY picks when all are empty


@dataclass(frozen=True, slots=True)
class CityConfig:
    """One joinable game's rule set (II §5); weather, block map and NPCs come later."""

    letter: str
    name: str
    map: int
    max_multiplier: int
    tower_output: int
    max_module_level: int
    capacity: int
    team_policy: str
    balance_teams: tuple[int, ...]
    banned_modules: tuple[int, ...]
    enabled: bool


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Everything the app needs to start."""

    server: ServerConfig
    cities: tuple[CityConfig, ...]  # sorted by letter


_REQUIRED: Any = object()  # sentinel: the key has no default


class _Table:
    """A TOML table with typed, key-checked access that reports errors with file and key."""

    def __init__(self, path: Path, data: Mapping[str, Any]) -> None:
        self._path = path
        self._data = data
        self._seen: set[str] = set()

    def fail(self, key: str, problem: str) -> ConfigError:
        return ConfigError(f"{self._path}: {key}: {problem}")

    def _get(self, key: str, default: Any, kind: type, kind_name: str) -> Any:
        self._seen.add(key)
        if key not in self._data:
            if default is _REQUIRED:
                raise self.fail(key, "missing required key")
            return default
        value = self._data[key]
        # bool is an int subclass in Python; a TOML true is never a valid number.
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            raise self.fail(key, f"expected {kind_name}, got {type(value).__name__}")
        return value

    def string(self, key: str, default: Any = _REQUIRED) -> str:
        return str(self._get(key, default, str, "a string"))

    def integer(self, key: str, default: Any = _REQUIRED, *, low: int, high: int) -> int:
        value: int = self._get(key, default, int, "an integer")
        if not low <= value <= high:
            raise self.fail(key, f"{value} is outside {low}..{high}")
        return value

    def boolean(self, key: str, default: Any = _REQUIRED) -> bool:
        return bool(self._get(key, default, bool, "a boolean"))

    def int_list(self, key: str, default: Any, *, low: int, high: int) -> tuple[int, ...]:
        value: list[Any] = self._get(key, default, list, "a list")
        if value is default:
            return tuple(default)
        for item in value:
            if not isinstance(item, int) or isinstance(item, bool):
                raise self.fail(key, f"expected a list of integers, found {item!r}")
            if not low <= item <= high:
                raise self.fail(key, f"{item} is outside {low}..{high}")
        return tuple(value)

    def unique_nonempty(self, key: str, values: tuple[int, ...]) -> tuple[int, ...]:
        """Reject an empty tuple or one with repeats (a typo that should fail at startup)."""
        if not values:
            raise self.fail(key, "must not be empty")
        if len(set(values)) != len(values):
            raise self.fail(key, "must not repeat a value")
        return values

    def done(self) -> None:
        """Reject any key that was never asked for."""
        unknown = sorted(set(self._data) - self._seen)
        if unknown:
            raise self.fail(unknown[0], "unknown key")


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read: {exc.strerror or exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc


def _parse_server(path: Path, data: Mapping[str, Any]) -> ServerConfig:
    table = _Table(path, data)
    base = path.parent
    config = ServerConfig(
        host=table.string("host", policy.DEFAULT_HOST),
        port=table.integer("port", policy.DEFAULT_PORT, low=0, high=65535),
        data_dir=base / table.string("data_dir", policy.DEFAULT_DATA_DIR),
        cities_dir=base / table.string("cities_dir", policy.DEFAULT_CITIES_DIR),
        log_level=table.string("log_level", policy.DEFAULT_LOG_LEVEL).upper(),
        default_city=table.string("default_city", policy.DEFAULT_CITY_LETTER),
    )
    table.done()
    if config.log_level not in LOG_LEVELS:
        raise table.fail("log_level", f"must be one of {', '.join(LOG_LEVELS)}")
    return config


def _parse_city(path: Path, data: Mapping[str, Any]) -> CityConfig:
    table = _Table(path, data)
    letter = table.string("letter")
    if len(letter) != 1 or not "A" <= letter <= "Z":
        raise table.fail("letter", f"{letter!r} is not one uppercase letter A-Z")
    name = table.string("name")
    if not 1 <= len(name) <= policy.MAX_CITY_NAME_LENGTH:
        raise table.fail("name", f"must be 1..{policy.MAX_CITY_NAME_LENGTH} characters")
    if not all(policy.PRINTABLE_LOW <= ch <= policy.PRINTABLE_HIGH for ch in name):
        raise table.fail("name", "must be printable ASCII (0x20-0x7e)")
    team_policy = table.string("team_policy", policy.DEFAULT_TEAM_POLICY)
    if team_policy not in policy.TEAM_POLICIES:
        raise table.fail("team_policy", f"must be one of {', '.join(policy.TEAM_POLICIES)}")
    config = CityConfig(
        letter=letter,
        name=name,
        # Any map number: only 1-3 ship with the client (II §5).
        map=table.integer("map", low=1, high=sys.maxsize),
        max_multiplier=table.integer("max_multiplier", low=1, high=policy.MAX_MULTIPLIER_CEILING),
        tower_output=table.integer("tower_output", low=0, high=policy.MAX_TOWER_OUTPUT),
        max_module_level=table.integer("max_module_level", low=0, high=policy.MAX_MODULE_LEVEL),
        capacity=table.integer(
            "capacity", policy.DEFAULT_CITY_CAPACITY, low=1, high=policy.MAX_CITY_CAPACITY
        ),
        team_policy=team_policy,
        balance_teams=table.unique_nonempty(
            "balance_teams",
            table.int_list(
                "balance_teams", tuple(range(policy.TEAM_COUNT)), low=0, high=policy.TEAM_COUNT - 1
            ),
        ),
        # The modules milestone will check each entry against the real catalog (I §9.3.1).
        banned_modules=table.int_list(
            "banned_modules",
            (),
            low=policy.MODULE_INDEX_LOW,
            high=policy.MODULE_INDEX_HIGH,
        ),
        enabled=table.boolean("enabled", True),
    )
    table.done()
    return config


def load_config(path: Path | str) -> AppConfig:
    """Read ``path`` (``server.toml``) and every ``*.toml`` in its ``cities_dir``."""
    server_path = Path(path)
    server = _parse_server(server_path, _read_toml(server_path))
    if not server.cities_dir.is_dir():
        raise ConfigError(f"{server_path}: cities_dir: {server.cities_dir} is not a directory")
    cities: dict[str, CityConfig] = {}
    for city_path in sorted(server.cities_dir.glob("*.toml")):
        city = _parse_city(city_path, _read_toml(city_path))
        if city.letter in cities:
            raise ConfigError(f"{city_path}: letter: duplicate city letter {city.letter!r}")
        cities[city.letter] = city
    if server.default_city not in cities:
        raise ConfigError(
            f"{server_path}: default_city: {server.default_city!r} is not a loaded city"
        )
    return AppConfig(server=server, cities=tuple(cities[k] for k in sorted(cities)))
