from pathlib import Path

import pytest

from opencs.config import ConfigError, load_config
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


def test_default_city_must_be_loaded(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"A": CITY}, server='default_city = "Z"\n')
    with pytest.raises(ConfigError, match="default_city"):
        load_config(path)


def test_unknown_server_key_and_bad_types(tmp_path: Path) -> None:
    for text, key in [
        ('default_city = "A"\nbogus = 1\n', "bogus"),
        ('default_city = "A"\nport = "x"\n', "port"),
        ('default_city = "A"\nport = 70000\n', "port"),
        ('default_city = "A"\nlog_level = "LOUD"\n', "log_level"),
    ]:
        with pytest.raises(ConfigError, match=f": {key}:"):
            load_config(write_config(tmp_path, {"A": CITY}, server=text))


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
    assert (bare.host, bare.port, bare.log_level) == (
        shipped.host,
        shipped.port,
        shipped.log_level,
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
