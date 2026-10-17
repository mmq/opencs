from pathlib import Path

import pytest

from opencs.config import ConfigError, load_config

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
        ('name = "SEVENTEEN CHARS XX"', "name"),
        ('name = ""', "name"),
        ('name = "X\\u0001Y"', "name"),
        ('name = "CAF\\u00c9"', "name"),
        ("map = 0", "map"),
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
