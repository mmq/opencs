"""Typed, validated configuration read from TOML (DESIGN §2, §4).

``load_config`` turns ``server.toml`` and the per-city files into frozen dataclasses. Unknown keys
and wrong types raise ``ConfigError`` naming the file and the key, so a typo fails at startup.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opencs import policy
from opencs.messages import limits
from opencs.messages.catalog import is_module_index

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(Exception):
    """A configuration file is unreadable or holds a bad value."""


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    """``[logging]`` in ``server.toml`` (DESIGN §6): the level of everything, a level for chosen
    layers, the line format and an optional rotating file."""

    level: str = policy.DEFAULT_LOG_LEVEL
    #: Layer name (``policy.LOG_LAYERS``) -> level, overriding ``level`` for ``opencs.<layer>``.
    levels: tuple[tuple[str, str], ...] = ()
    format: str = policy.DEFAULT_LOG_FORMAT
    file: Path | None = None
    max_bytes: int = policy.DEFAULT_LOG_MAX_BYTES
    backups: int = policy.DEFAULT_LOG_BACKUPS


@dataclass(frozen=True, slots=True)
class CaptureConfig:
    """``[capture]``: record each connection's wire traffic in the replay-fixture format
    (DESIGN §6). ``callsigns`` limits it to those pilots (empty: everyone)."""

    enabled: bool = False
    directory: Path = Path(policy.DEFAULT_CAPTURE_DIR)
    callsigns: tuple[str, ...] = ()
    max_bytes: int = policy.DEFAULT_CAPTURE_MAX_BYTES


@dataclass(frozen=True, slots=True)
class AdminConfig:
    """``[admin]``: the local operator channel (DESIGN §6). Off unless ``port`` is given. The token
    is ``token`` if set, else the environment variable ``token_env`` names; the channel does not
    start without one."""

    port: int | None = None
    token: str | None = None
    token_env: str = policy.ADMIN_TOKEN_ENV


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Process-wide settings (``server.toml``)."""

    host: str
    port: int
    data_dir: Path
    cities_dir: Path
    logging: LoggingConfig
    capture: CaptureConfig
    admin: AdminConfig
    default_city: str  # II §4.2: the city a bare /PLAY picks when all are empty
    #: Upper case; empty turns guests off (II §3).
    guest_names: tuple[str, ...] = policy.DEFAULT_GUEST_NAMES


@dataclass(frozen=True, slots=True)
class AmbientStep:
    """One step of a city's weather program (I §8.20, II §5): ``after`` seconds since the
    previous step (since the game began, for the first), then whichever of the palette ``light``
    (0-4), ``rain`` (0-3) and ``lightning`` it names change. The program loops."""

    after: float
    light: int | None = None
    rain: int | None = None
    lightning: bool | None = None


@dataclass(frozen=True, slots=True)
class CityConfig:
    """One joinable game's rule set (II §5, II §16, II §5)."""

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
    #: The territory map: ``BLOCK_SIDE`` rows (cell Z 0..7, top to bottom) of ``BLOCK_SIDE`` classes
    #: 0-3 (cell X 0..7), or None for the client's default, every block worth 1 (II §5).
    blocks: tuple[tuple[int, ...], ...] | None = None
    #: Resident Sentinels, the seconds before a destroyed one is replaced, and the seconds between
    #: free Munchers (0 = none). II §16.3.
    sentinels: int = 0
    sentinel_respawn: float = policy.DEFAULT_SENTINEL_RESPAWN
    muncher_every: float = 0.0
    #: The weather program, looped from the moment the city's game starts (empty = none).
    ambient: tuple[AmbientStep, ...] = ()


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Everything the app needs to start."""

    server: ServerConfig
    cities: tuple[CityConfig, ...]  # sorted by letter


@dataclass(frozen=True, slots=True)
class ConfigFile:
    """The file the running config came from, for ``reload`` (SIGHUP, the admin channel):
    ``server`` is what the file said when it was read, before command-line overrides."""

    path: Path
    server: ServerConfig


_REQUIRED: Any = object()  # sentinel: the key has no default


class _Table:
    """A TOML table with typed, key-checked access that reports errors with file and key."""

    def __init__(self, path: Path, data: Mapping[str, Any], prefix: str = "") -> None:
        self._path = path
        self._data = data
        self._prefix = prefix
        self._seen: set[str] = set()

    def fail(self, key: str, problem: str) -> ConfigError:
        return ConfigError(f"{self._path}: {self._prefix}{key}: {problem}")

    def _get(self, key: str, default: Any, kind: type | tuple[type, ...], kind_name: str) -> Any:
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

    def optional_integer(self, key: str, *, low: int, high: int) -> int | None:
        """An integer in ``low..high`` or None when the key is absent."""
        if key not in self._data:
            self._seen.add(key)
            return None
        return self.integer(key, low=low, high=high)

    def optional_boolean(self, key: str) -> bool | None:
        if key not in self._data:
            self._seen.add(key)
            return None
        return self.boolean(key)

    def number(self, key: str, default: Any = _REQUIRED, *, low: float, high: float) -> float:
        """An integer or float in ``low..high``."""
        value: Any = self._get(key, default, (int, float), "a number")
        if isinstance(value, bool):
            raise self.fail(key, "expected a number, got bool")
        if not low <= value <= high:
            raise self.fail(key, f"{value} is outside {low:g}..{high:g}")
        return float(value)

    def has(self, key: str) -> bool:
        return key in self._data

    def table(self, key: str) -> _Table:
        """A ``[key]`` sub-table (empty when absent), checked like this one."""
        data: Mapping[str, Any] = self._get(key, {}, dict, "a table")
        return _Table(self._path, data, f"{self._prefix}{key}.")

    def level(self, key: str, default: str) -> str:
        """A log level name, upper-cased."""
        value = self.string(key, default).upper()
        if value not in LOG_LEVELS:
            raise self.fail(key, f"must be one of {', '.join(LOG_LEVELS)}")
        return value

    def tables(self, key: str) -> list[Mapping[str, Any]]:
        """An array of TOML tables (``[[key]]``), or an empty list."""
        rows: list[Any] = self._get(key, [], list, "an array of tables")
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                raise self.fail(key, f"entry {i} is not a table")
        return rows

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

    def str_list(self, key: str, default: Any) -> tuple[str, ...]:
        value: list[Any] = self._get(key, default, (list, tuple), "a list")
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise self.fail(key, f"expected a list of non-empty strings, found {item!r}")
        return tuple(value)

    def string_list(self, key: str) -> tuple[str, ...]:
        value: list[Any] = self._get(key, [], list, "a list of strings")
        for item in value:
            if not isinstance(item, str):
                raise self.fail(key, f"expected a list of strings, found {item!r}")
        return tuple(value)

    def unique_nonempty(self, key: str, values: tuple[int, ...]) -> tuple[int, ...]:
        """Reject an empty tuple or one with repeats (a typo that should fail at startup)."""
        if not values:
            raise self.fail(key, "must not be empty")
        if len(set(values)) != len(values):
            raise self.fail(key, "must not repeat a value")
        return values

    def block_rows(self, key: str) -> tuple[tuple[int, ...], ...] | None:
        """A block-class map written as ``BLOCK_SIDE`` strings of ``BLOCK_SIDE`` digits, or None."""
        rows: list[str] | None = self._get(key, None, list, "a list of strings")
        if rows is None:
            return None
        side = limits.BLOCK_SIDE
        if len(rows) != side:
            raise self.fail(key, f"needs {side} rows, found {len(rows)}")
        parsed: list[tuple[int, ...]] = []
        for i, row in enumerate(rows):
            if not isinstance(row, str) or len(row) != side:
                raise self.fail(key, f"row {i} must be a string of {side} digits")
            if any(not "0" <= ch <= str(limits.BLOCK_CLASS_MAX) for ch in row):
                raise self.fail(key, f"row {i} has a class outside 0..{limits.BLOCK_CLASS_MAX}")
            parsed.append(tuple(int(ch) for ch in row))
        return tuple(parsed)

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


def _parse_ambient(path: Path, table: _Table) -> tuple[AmbientStep, ...]:
    """The weather program: each step's values in range, light changes at least
    ``MIN_LIGHT_GAP_S`` apart round the loop (II §5), a period of at least
    ``MIN_AMBIENT_PERIOD``."""
    rows = table.tables("ambient")
    if len(rows) > policy.MAX_AMBIENT_STEPS:
        raise table.fail("ambient", f"more than {policy.MAX_AMBIENT_STEPS} steps")
    steps: list[AmbientStep] = []
    for i, row in enumerate(rows):
        step = _Table(path, row)
        after = step.number("after", low=0, high=policy.MAX_AMBIENT_AFTER)
        light = step.optional_integer("light", low=0, high=limits.AMBIENT_PALETTE_MAX)
        rain = step.optional_integer("rain", low=0, high=limits.AMBIENT_RAIN_MAX)
        lightning = step.optional_boolean("lightning")
        try:
            step.done()
        except ConfigError as exc:
            raise ConfigError(f"{exc} (ambient step {i + 1})") from exc
        steps.append(AmbientStep(after, light, rain, lightning))
    if not steps:
        return ()
    period = sum(s.after for s in steps)
    if period < policy.MIN_AMBIENT_PERIOD:
        raise table.fail(
            "ambient",
            f"the program's total period must be at least {policy.MIN_AMBIENT_PERIOD:g} s",
        )
    at = 0.0
    light_times: list[float] = []
    for s in steps:
        at += s.after
        if s.light is not None:
            light_times.append(at)
    # Each light change against the next, the last against the first one period later.
    following = [*light_times[1:], light_times[0] + period] if light_times else []
    for earlier, later in zip(light_times, following, strict=True):
        if later - earlier < policy.MIN_LIGHT_GAP_S:
            raise table.fail(
                "ambient",
                f"light changes {later - earlier:g} s apart (the loop's wrap included); the "
                f"client's crossfade needs {policy.MIN_LIGHT_GAP_S:g} s (I §8.20)",
            )
    return tuple(steps)


def _parse_logging(path: Path, table: _Table) -> LoggingConfig:
    """``[logging]``: levels by name, a layer table limited to the known loggers, a rotating file
    (relative to the config file) with positive limits."""
    section = table.table("logging")
    levels_table = section.table("levels")
    levels = tuple(
        (layer, levels_table.level(layer, policy.DEFAULT_LOG_LEVEL))
        for layer in policy.LOG_LAYERS
        if levels_table.has(layer)
    )
    levels_table.done()
    file_name = section.string("file", "")
    config = LoggingConfig(
        level=section.level("level", policy.DEFAULT_LOG_LEVEL),
        levels=levels,
        format=section.string("format", policy.DEFAULT_LOG_FORMAT),
        file=path.parent / file_name if file_name else None,
        max_bytes=section.integer(
            "max_bytes", policy.DEFAULT_LOG_MAX_BYTES, low=1, high=policy.MAX_LOG_FILE_BYTES
        ),
        backups=section.integer(
            "backups", policy.DEFAULT_LOG_BACKUPS, low=0, high=policy.MAX_LOG_BACKUPS
        ),
    )
    section.done()
    try:
        config.format % {  # a format the logging module cannot apply fails here, not at 3 a.m.
            "asctime": "",
            "levelname": "",
            "name": "",
            "message": "",
            "threadName": "",
        }
    except (KeyError, ValueError, TypeError) as exc:
        raise section.fail("format", f"not a valid log format: {exc}") from exc
    return config


def _parse_capture(path: Path, table: _Table) -> CaptureConfig:
    section = table.table("capture")
    callsigns = section.string_list("callsigns")
    config = CaptureConfig(
        enabled=section.boolean("enabled", False),
        directory=path.parent / section.string("dir", policy.DEFAULT_CAPTURE_DIR),
        callsigns=tuple(c.upper() for c in callsigns),
        max_bytes=section.integer(
            "max_bytes", policy.DEFAULT_CAPTURE_MAX_BYTES, low=1000, high=policy.MAX_CAPTURE_BYTES
        ),
    )
    section.done()
    return config


def _parse_admin(table: _Table, game_port: int) -> AdminConfig:
    section = table.table("admin")
    port = section.optional_integer("port", low=policy.MIN_PORT, high=policy.MAX_PORT)
    token = section.string("token", "") or None
    config = AdminConfig(port, token, section.string("token_env", policy.ADMIN_TOKEN_ENV))
    section.done()
    if token is not None and len(token) < policy.ADMIN_TOKEN_MIN_LENGTH:
        raise section.fail("token", f"must be at least {policy.ADMIN_TOKEN_MIN_LENGTH} characters")
    if port and port == game_port:
        raise section.fail("port", "must differ from the game port")
    return config


def _parse_server(path: Path, data: Mapping[str, Any]) -> ServerConfig:
    table = _Table(path, data)
    base = path.parent
    game_port = table.integer(
        "port", policy.DEFAULT_PORT, low=policy.MIN_PORT, high=policy.MAX_PORT
    )
    config = ServerConfig(
        host=table.string("host", policy.DEFAULT_HOST),
        port=game_port,
        data_dir=base / table.string("data_dir", policy.DEFAULT_DATA_DIR),
        cities_dir=base / table.string("cities_dir", policy.DEFAULT_CITIES_DIR),
        logging=_parse_logging(path, table),
        capture=_parse_capture(path, table),
        admin=_parse_admin(table, game_port),
        default_city=table.string("default_city", policy.DEFAULT_CITY_LETTER),
        guest_names=tuple(
            name.strip().upper()
            for name in table.str_list("guest_names", policy.DEFAULT_GUEST_NAMES)
        ),
    )
    table.done()
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
        # Only maps 1-3 ship with the client, but any digit names a file (I §5.4, II §5).
        map=table.integer("map", low=policy.MAP_NUMBER_LOW, high=policy.MAP_NUMBER_HIGH),
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
        banned_modules=table.int_list(
            "banned_modules",
            (),
            low=policy.MODULE_INDEX_LOW,
            high=policy.MODULE_INDEX_HIGH,
        ),
        enabled=table.boolean("enabled", True),
        blocks=table.block_rows("blocks"),
        sentinels=table.integer("sentinels", 0, low=0, high=policy.MAX_SENTINELS),
        sentinel_respawn=table.number(
            "sentinel_respawn",
            policy.DEFAULT_SENTINEL_RESPAWN,
            low=policy.MIN_SPAWN_INTERVAL,
            high=policy.MAX_SPAWN_INTERVAL,
        ),
        muncher_every=_muncher_every(table),
        ambient=_parse_ambient(path, table),
    )
    table.done()
    for index in config.banned_modules:
        if not is_module_index(index):  # 10, 19, 20... are inside 1-47 but name no module
            raise table.fail("banned_modules", f"{index} is not a module index (I §9.3.1)")
    if config.blocks is not None:
        # I §9.2.6: the client's 16-bit income product must not overflow.
        total = sum(limits.BLOCK_VALUES[c] for row in config.blocks for c in row)
        if total * config.max_multiplier >= limits.SCORE_PRODUCT_LIMIT:
            raise table.fail(
                "blocks",
                f"grid total {total} x max_multiplier {config.max_multiplier} reaches "
                f"{limits.SCORE_PRODUCT_LIMIT}, which overflows the client's income",
            )
    return config


def _muncher_every(table: _Table) -> float:
    """0 (no free Munchers) or at least ``MIN_SPAWN_INTERVAL`` seconds, so a typo cannot make the
    city's tick send one per microsecond."""
    every = table.number("muncher_every", 0, low=0, high=policy.MAX_SPAWN_INTERVAL)
    if 0 < every < policy.MIN_SPAWN_INTERVAL:
        raise table.fail(
            "muncher_every", f"must be 0 or at least {policy.MIN_SPAWN_INTERVAL:g} seconds"
        )
    return every


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
