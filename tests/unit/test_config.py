from pathlib import Path

import pytest

from opencs import policy
from opencs.config import AmbientStep, CityConfig, ConfigError, LimitsConfig, load_config
from opencs.messages import limits

SHIPPED = Path(__file__).parents[2] / "config" / "server.toml"

CITY = """
letter = "A"
name = "TESTVILLE"
map = 1
max_multiplier = 8
tower_output = 1
max_module_level = 2
"""


def write_config(tmp_path: Path, cities: dict[str, str], server: str = "") -> Path:
    (tmp_path / "cities").mkdir(exist_ok=True)
    for name, text in cities.items():
        (tmp_path / "cities" / f"{name}.toml").write_text(text)
    path = tmp_path / "server.toml"
    path.write_text(server or 'default_city = "A"\n')
    return path


def with_line(line: str) -> str:
    """CITY with ``line`` replacing any existing assignment of the same key, or appended."""
    key = line.split("=", maxsplit=1)[0].strip()
    kept = [x for x in CITY.splitlines() if x.split("=")[0].strip() != key]
    return "\n".join([*kept, line])


def test_shipped_config_matches_spec_table() -> None:
    config = load_config(SHIPPED)
    rows = [
        (c.letter, c.name, c.map, c.max_multiplier, c.tower_output, c.max_module_level)
        for c in config.cities
    ]
    assert rows == [
        ("A", "CYBERCITY ONE", 1, 8, 1, 2),
        ("B", "CYBERCITY TWO", 2, 20, 1, 4),
        ("C", "CYBERCITY THREE", 3, 40, 0, 5),
    ]
    assert config.server.default_city == "B"
    assert all(c.capacity == 16 and c.enabled and not c.banned_modules for c in config.cities)
    assert all(c.balance_teams == (0, 1, 2, 3) for c in config.cities)
    assert config.server.port == 2399


def test_defaults_apply(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, {"A": CITY}))
    city = config.cities[0]
    assert (city.capacity, city.team_policy, city.enabled) == (16, "request-or-balance", True)
    assert config.server.host == "0.0.0.0"


@pytest.mark.parametrize(
    ("line", "key"),
    [
        ('letter = "a"', "letter"),
        ('letter = "AB"', "letter"),
        ('name = "SEVENTEEN CHARS X"', "name"),
        ('name = ""', "name"),
        ('name = "X\\u0001Y"', "name"),
        ('name = "CAF\\u00c9"', "name"),
        ("map = 0", "map"),
        ("map = 10", "map"),
        ("max_multiplier = 0", "max_multiplier"),
        ("max_module_level = -1", "max_module_level"),
        ("tower_output = -1", "tower_output"),
        ("balance_teams = [-1]", "balance_teams"),
        ("balance_teams = 3", "balance_teams"),
        ("balance_teams = [true]", "balance_teams"),
        ("banned_modules = [-1]", "banned_modules"),
        ("max_multiplier = 52", "max_multiplier"),
        ("max_module_level = 6", "max_module_level"),
        ("tower_output = 3", "tower_output"),
        ('map = "one"', "map"),
        ("map = true", "map"),
        ("capacity = 0", "capacity"),
        ('team_policy = "chaos"', "team_policy"),
        ("balance_teams = [4]", "balance_teams"),
        ('banned_modules = ["x"]', "banned_modules"),
        ('enabled = "yes"', "enabled"),
        ("surprise = 1", "surprise"),
    ],
)
def test_bad_city_value_names_file_and_key(tmp_path: Path, line: str, key: str) -> None:
    path = write_config(tmp_path, {"A": with_line(line)})
    with pytest.raises(ConfigError) as info:
        load_config(path)
    assert "A.toml" in str(info.value)
    assert f": {key}:" in str(info.value)


def test_missing_required_key(tmp_path: Path) -> None:
    text = "\n".join(x for x in CITY.splitlines() if not x.startswith("map"))
    with pytest.raises(ConfigError, match=r"map: missing"):
        load_config(write_config(tmp_path, {"A": text}))


def test_duplicate_letter(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="duplicate"):
        load_config(write_config(tmp_path, {"A": CITY, "A2": CITY}))


def test_default_city_must_be_enabled(tmp_path: Path) -> None:
    path = write_config(
        tmp_path, {"A": with_line("enabled = false"), "B": CITY.replace('"A"', '"B"')}
    )
    with pytest.raises(ConfigError, match="default_city: 'A' is disabled"):
        load_config(path)


def test_default_city_must_be_loaded(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"A": CITY}, server='default_city = "Z"\n')
    with pytest.raises(ConfigError, match="default_city"):
        load_config(path)


def test_guest_names_default_to_joingenie_and_may_be_listed_or_turned_off(tmp_path: Path) -> None:
    assert load_config(write_config(tmp_path, {"A": CITY})).server.guest_names == ("JOINGENIE",)
    for line, names in [('["JoinGenie", "visitor"]', ("JOINGENIE", "VISITOR")), ("[]", ())]:
        path = write_config(
            tmp_path, {"A": CITY}, server=f'default_city = "A"\nguest_names = {line}\n'
        )
        assert load_config(path).server.guest_names == names
    for bad in ('"JOINGENIE"', '[""]', "[1]"):
        path = write_config(
            tmp_path, {"A": CITY}, server=f'default_city = "A"\nguest_names = {bad}\n'
        )
        with pytest.raises(ConfigError, match="guest_names"):
            load_config(path)


def test_unknown_server_key_and_bad_types(tmp_path: Path) -> None:
    for text, key in [
        ('default_city = "A"\nbogus = 1\n', "bogus"),
        ('default_city = "A"\nport = "x"\n', "port"),
        ('default_city = "A"\nport = 70000\n', "port"),
        ('default_city = "A"\n[logging]\nlevel = "LOUD"\n', "logging.level"),
        ('default_city = "A"\n[logging.levels]\nbogus = "INFO"\n', "logging.levels.bogus"),
        ('default_city = "A"\n[logging.levels]\nnet = "LOUD"\n', "logging.levels.net"),
        ('default_city = "A"\n[logging]\nmax_bytes = 0\n', "logging.max_bytes"),
        ('default_city = "A"\n[logging]\nformat = "%(nope)s"\n', "logging.format"),
    ]:
        with pytest.raises(ConfigError, match=f": {key}:"):
            load_config(write_config(tmp_path, {"A": CITY}, server=text))


def test_any_standard_log_record_field_is_a_valid_format(tmp_path: Path) -> None:
    fmt = "%(asctime)s %(process)d %(funcName)s:%(lineno)d %(message)s"
    server = f'default_city = "A"\n[logging]\nformat = "{fmt}"\n'
    config = load_config(write_config(tmp_path, {"A": CITY}, server=server))
    assert config.server.logging.format == fmt


def test_unreadable_and_invalid_files(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "nope.toml")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(write_config(tmp_path, {"A": "letter = = 1"}))


def test_cities_sorted_by_letter(tmp_path: Path) -> None:
    b = CITY.replace('"A"', '"B"')
    config = load_config(write_config(tmp_path, {"1": b, "2": CITY}))
    assert [c.letter for c in config.cities] == ["A", "B"]


def test_capacity_ceiling_is_sixteen(tmp_path: Path) -> None:
    city = load_config(write_config(tmp_path, {"A": with_line("capacity = 16")})).cities[0]
    assert city.capacity == 16
    with pytest.raises(ConfigError, match=": capacity:"):
        load_config(write_config(tmp_path, {"A": with_line("capacity = 17")}))


@pytest.mark.parametrize(
    "line",
    [
        "balance_teams = []",
        "balance_teams = [1, 1, 1]",
        "banned_modules = [0]",
        "banned_modules = [48]",
        "banned_modules = [99]",
        "banned_modules = [10]",  # inside 1-47 but names no module (I §9.3.1)
        "banned_modules = [6, 38]",
    ],
)
def test_empty_duplicate_and_out_of_catalog_lists(tmp_path: Path, line: str) -> None:
    with pytest.raises(ConfigError, match=f": {line.split(maxsplit=1)[0]}:"):
        load_config(write_config(tmp_path, {"A": with_line(line)}))


@pytest.mark.parametrize(
    ("line", "attr", "expected"),
    [
        ('name = "SIXTEEN CHARS XX"', "name", "SIXTEEN CHARS XX"),
        ('name = "X"', "name", "X"),
        ("max_multiplier = 1", "max_multiplier", 1),
        ("max_multiplier = 51", "max_multiplier", 51),
        ("max_module_level = 0", "max_module_level", 0),
        ("max_module_level = 5", "max_module_level", 5),
        ("tower_output = 0", "tower_output", 0),
        ("tower_output = 2", "tower_output", 2),
        ("capacity = 1", "capacity", 1),
        ("balance_teams = [0, 3]", "balance_teams", (0, 3)),
        ("banned_modules = [1, 47]", "banned_modules", (1, 47)),
    ],
)
def test_values_on_a_bound_are_accepted(
    tmp_path: Path, line: str, attr: str, expected: object
) -> None:
    city = load_config(write_config(tmp_path, {"A": with_line(line)})).cities[0]
    assert getattr(city, attr) == expected


def test_code_defaults_match_the_shipped_file(tmp_path: Path) -> None:
    """Leaving keys out of server.toml must not move the state directory."""
    shipped = load_config(SHIPPED).server
    bare = load_config(write_config(tmp_path, {"A": CITY})).server
    assert bare.data_dir.relative_to(tmp_path) == shipped.data_dir.relative_to(SHIPPED.parent)
    assert (bare.host, bare.port, bare.logging.level) == (
        shipped.host,
        shipped.port,
        shipped.logging.level,
    )


def test_map_number_may_be_any_single_digit_from_one(tmp_path: Path) -> None:
    # I §5.4: <rev> repeats the map number and must be 0-9, so 9 is the last map a city can name.
    config = load_config(write_config(tmp_path, {"A": with_line("map = 9")}))
    assert config.cities[0].map == 9


def blocks_line(rows: list[str]) -> str:
    return "blocks = [" + ", ".join(f'"{r}"' for r in rows) + "]"


GOOD_ROWS = ["00000000"] * 7 + ["01230123"]


def test_blocks_are_read_row_by_row_and_default_to_none(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, {"A": with_line(blocks_line(GOOD_ROWS))}))
    assert config.cities[0].blocks == tuple(tuple(int(c) for c in r) for r in GOOD_ROWS)
    config = load_config(write_config(tmp_path, {"A": CITY}))
    assert config.cities[0].blocks is None


@pytest.mark.parametrize(
    "rows",
    [
        GOOD_ROWS[:-1],  # seven rows
        [*GOOD_ROWS, "00000000"],  # nine rows
        [*GOOD_ROWS[:-1], "0123012"],  # a short row
        [*GOOD_ROWS[:-1], "012301230"],  # a long row
        [*GOOD_ROWS[:-1], "01230124"],  # class 4: the client indexes its table unchecked
        [*GOOD_ROWS[:-1], "0123012x"],
    ],
)
def test_bad_blocks_fail_naming_the_key(tmp_path: Path, rows: list[str]) -> None:
    with pytest.raises(ConfigError, match=r"A\.toml: blocks:"):
        load_config(write_config(tmp_path, {"A": with_line(blocks_line(rows))}))


def test_blocks_must_be_a_list_of_strings(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="blocks"):
        load_config(write_config(tmp_path, {"A": with_line("blocks = 5")}))
    with pytest.raises(ConfigError, match="blocks"):
        load_config(write_config(tmp_path, {"A": with_line("blocks = [1,2,3,4,5,6,7,8]")}))


def test_the_score_overflow_constraint_is_checked_at_the_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every map under the 51 ceiling is safe (640 x 51 < 32 768), so the check is a guard: move
    the limit to the exact product to see both sides of it."""
    text = with_line("max_multiplier = 8") + "\n" + blocks_line(["00111100"] * 8)
    total = 8 * (4 * 2 + 4 * 1)  # per row: four class-1 blocks worth 2, four class-0 worth 1
    assert total == 96
    product = total * 8
    monkeypatch.setattr(limits, "SCORE_PRODUCT_LIMIT", product + 1)
    load_config(write_config(tmp_path, {"A": text}))  # product just under the limit
    monkeypatch.setattr(limits, "SCORE_PRODUCT_LIMIT", product)
    with pytest.raises(ConfigError, match=r"blocks: grid total 96 x max_multiplier 8"):
        load_config(write_config(tmp_path, {"A": text}))


def test_the_largest_possible_map_fits_the_largest_multiplier(tmp_path: Path) -> None:
    text = with_line("max_multiplier = 51") + "\n" + blocks_line(["33333333"] * 8)
    assert load_config(write_config(tmp_path, {"A": text})).cities[0].max_multiplier == 51


def test_a_ban_may_name_any_catalog_module(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, {"A": with_line("banned_modules = [1, 47, 32]")}))
    assert config.cities[0].banned_modules == (1, 47, 32)


# --- The living city: NPC population and the weather program (II §16, II §5) ------------


def step(after: float, **fields: object) -> str:
    body = "".join(f"{k} = {str(v).lower()}\n" for k, v in fields.items())
    return f"\n[[ambient]]\nafter = {after}\n{body}"


def load_city(tmp_path: Path, extra: str) -> CityConfig:
    return load_config(write_config(tmp_path, {"A": CITY + extra})).cities[0]


def test_the_shipped_cities_carry_the_reference_populations_and_programs() -> None:
    a, b, c = load_config(SHIPPED).cities
    assert (a.sentinels, a.muncher_every, a.ambient) == (0, 0, ())
    assert (b.sentinels, b.sentinel_respawn, b.muncher_every) == (2, 300, 0)
    assert b.ambient == (AmbientStep(600, rain=2), AmbientStep(300, rain=0))
    assert (c.sentinels, c.sentinel_respawn, c.muncher_every) == (4, 180, 600)
    assert c.ambient == (
        AmbientStep(300, light=3),
        AmbientStep(300, rain=3, lightning=True),
        AmbientStep(240, rain=0, lightning=False),
        AmbientStep(300, light=0),
    )


def test_a_city_without_npcs_or_a_program_has_none(tmp_path: Path) -> None:
    city = load_city(tmp_path, "")
    assert (city.sentinels, city.muncher_every, city.ambient) == (0, 0, ())
    assert city.sentinel_respawn == policy.DEFAULT_SENTINEL_RESPAWN


@pytest.mark.parametrize(
    "line",
    [
        "sentinels = 49",
        "sentinels = -1",
        "sentinel_respawn = 0",
        "sentinel_respawn = 86401",
        "sentinel_respawn = 0.999",
        "muncher_every = 0.999",
        "muncher_every = 0.000001",
        "muncher_every = -1",
        "muncher_every = true",
        'muncher_every = "600"',
    ],
)
def test_bad_population_values_name_the_key(tmp_path: Path, line: str) -> None:
    with pytest.raises(ConfigError, match=line.split(" ", maxsplit=1)[0]):
        load_config(write_config(tmp_path, {"A": with_line(line)}))


def test_muncher_every_may_be_off_or_at_the_floor(tmp_path: Path) -> None:
    assert load_city(tmp_path, "muncher_every = 0\n").muncher_every == 0
    floor = policy.MIN_SPAWN_INTERVAL
    assert load_city(tmp_path, f"muncher_every = {floor}\n").muncher_every == floor


def test_population_values_on_a_bound_are_accepted(tmp_path: Path) -> None:
    city = load_city(tmp_path, "sentinels = 48\nsentinel_respawn = 1\nmuncher_every = 86400\n")
    assert (city.sentinels, city.sentinel_respawn, city.muncher_every) == (48, 1.0, 86400.0)


@pytest.mark.parametrize(
    ("steps", "why"),
    [
        (step(10, light=5), "light"),
        (step(10, light=-1), "light"),
        (step(10, rain=4), "rain"),
        (step(10, rain=-1), "rain"),
        (step(10, lightning=1), "lightning"),
        (step(-1, rain=1), "after"),
        (step(604801, rain=1), "after"),
        (step(0, rain=1), "period"),
        (step(policy.MIN_AMBIENT_PERIOD - 0.01, rain=1), "period"),
        (step(0.000001, rain=1), "period"),
        (step(10, light=1) + step(10, light=2) + step(100, rain=1), "55"),
        (step(54, light=1), "55"),
        (step(30, light=1) + step(54, light=2), "55"),
    ],
)
def test_bad_programs_are_refused(tmp_path: Path, steps: str, why: str) -> None:
    with pytest.raises(ConfigError, match=why):
        load_city(tmp_path, steps)


def test_light_changes_may_be_exactly_55_seconds_apart_and_the_wrap_counts(tmp_path: Path) -> None:
    ok = load_city(tmp_path, step(55, light=1) + step(55, light=2))
    assert [s.light for s in ok.ambient] == [1, 2]
    # 55 s between the two light changes, but 14 s from the last round the loop to the first.
    with pytest.raises(ConfigError, match="55"):
        load_city(tmp_path, step(10, light=1) + step(55, light=2) + step(4, rain=1))


def test_a_rain_only_program_has_no_light_spacing_to_check(tmp_path: Path) -> None:
    city = load_city(tmp_path, step(3, rain=1) + step(2, rain=0, lightning=True))
    assert city.ambient == (AmbientStep(3, rain=1), AmbientStep(2, rain=0, lightning=True))
    # Exactly the floor is accepted (the refusals just under it are in the table above).
    assert sum(s.after for s in city.ambient) == policy.MIN_AMBIENT_PERIOD


def test_an_unknown_key_in_a_step_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="notes"):
        load_city(tmp_path, '\n[[ambient]]\nafter = 1\nnotes = "x"\n')


def test_logging_table_is_read(tmp_path: Path) -> None:
    text = (
        'default_city = "A"\n[logging]\nlevel = "debug"\nfile = "logs/x.log"\n'
        'max_bytes = 100\nbackups = 2\n[logging.levels]\nnet = "warning"\nworld = "ERROR"\n'
    )
    logging_config = load_config(write_config(tmp_path, {"A": CITY}, server=text)).server.logging
    assert logging_config.level == "DEBUG"
    assert logging_config.levels == (("net", "WARNING"), ("world", "ERROR"))
    assert logging_config.file == tmp_path / "logs" / "x.log"
    assert (logging_config.max_bytes, logging_config.backups) == (100, 2)


def test_capture_table_is_read_and_off_by_default(tmp_path: Path) -> None:
    assert not load_config(write_config(tmp_path, {"A": CITY})).server.capture.enabled
    text = (
        'default_city = "A"\n[capture]\nenabled = true\ndir = "caps"\n'
        'callsigns = ["alpha", "BRAVO"]\nmax_bytes = 5000\n'
    )
    capture = load_config(write_config(tmp_path, {"A": CITY}, server=text)).server.capture
    assert capture.enabled and capture.directory == tmp_path / "caps"
    assert capture.callsigns == ("ALPHA", "BRAVO") and capture.max_bytes == 5000


@pytest.mark.parametrize(
    ("line", "key"),
    [("max_bytes = 10", "max_bytes"), ("callsigns = [1]", "callsigns"), ("enabled = 1", "enabled")],
)
def test_bad_capture_values_name_the_key(tmp_path: Path, line: str, key: str) -> None:
    text = f'default_city = "A"\n[capture]\n{line}\n'
    with pytest.raises(ConfigError, match=f"capture.{key}:"):
        load_config(write_config(tmp_path, {"A": CITY}, server=text))


def test_admin_is_off_by_default_and_reads_its_table(tmp_path: Path) -> None:
    assert load_config(write_config(tmp_path, {"A": CITY})).server.admin.port is None
    text = 'default_city = "A"\n[admin]\nport = 2400\ntoken = "0123456789abcdef"\n'
    admin = load_config(write_config(tmp_path, {"A": CITY}, server=text)).server.admin
    assert (admin.port, admin.token, admin.token_env) == (
        2400,
        "0123456789abcdef",
        "OPENCS_ADMIN_TOKEN",
    )
    text = 'default_city = "A"\n[admin]\nport = 0\ntoken_env = "MY_TOKEN"\n'
    admin = load_config(write_config(tmp_path, {"A": CITY}, server=text)).server.admin
    assert (admin.port, admin.token, admin.token_env) == (0, None, "MY_TOKEN")


@pytest.mark.parametrize(
    ("body", "key"),
    [
        ('port = 2400\ntoken = "short"', "admin.token"),
        ('port = 2399\ntoken = "0123456789abcdef"', "admin.port"),  # the game port
        ("port = 70000", "admin.port"),
        ('port = "x"', "admin.port"),
        ("bogus = 1", "admin.bogus"),
    ],
)
def test_bad_admin_values_name_the_key(tmp_path: Path, body: str, key: str) -> None:
    text = f'default_city = "A"\n[admin]\n{body}\n'
    with pytest.raises(ConfigError, match=f"{key}:"):
        load_config(write_config(tmp_path, {"A": CITY}, server=text))


def test_registration_is_open_by_default_and_may_be_closed(tmp_path: Path) -> None:
    assert load_config(write_config(tmp_path, {"A": CITY})).server.registration == "open"
    path = write_config(
        tmp_path, {"A": CITY}, server='default_city = "A"\nregistration = "closed"\n'
    )
    assert load_config(path).server.registration == "closed"
    path = write_config(
        tmp_path, {"A": CITY}, server='default_city = "A"\nregistration = "maybe"\n'
    )
    with pytest.raises(ConfigError, match="registration"):
        load_config(path)


def test_the_limits_default_to_policy_and_may_be_tuned(tmp_path: Path) -> None:
    assert load_config(write_config(tmp_path, {"A": CITY})).server.limits == LimitsConfig()
    text = (
        'default_city = "A"\n[limits]\nmax_connections = 300\nmax_connections_per_address = 20\n'
        "max_connections_per_subnet = 40\nmax_guests = 0\nmin_password_length = 10\n"
    )
    limits = load_config(write_config(tmp_path, {"A": CITY}, server=text)).server.limits
    assert (limits.max_connections, limits.max_connections_per_address) == (300, 20)
    assert (limits.max_connections_per_subnet, limits.max_guests) == (40, 0)
    assert limits.min_password_length == 10
    assert limits.max_accounts == policy.MAX_ACCOUNTS


@pytest.mark.parametrize(
    "line",
    [
        "max_connections = 0",
        "max_connections_per_address = 200",  # more than the total
        f"max_connections_per_address = {policy.MAX_HELD_PER_ADDRESS}",  # not above the held games
        "max_connections_per_subnet = 2",  # below the per-address limit
        "max_unauthenticated_per_address = 99",
        "min_password_length = 0",
        "max_guests = -1",
        "unknown_limit = 3",
    ],
)
def test_a_limit_out_of_range_stops_the_server(tmp_path: Path, line: str) -> None:
    path = write_config(tmp_path, {"A": CITY}, server=f'default_city = "A"\n[limits]\n{line}\n')
    with pytest.raises(ConfigError, match="limits"):
        load_config(path)
