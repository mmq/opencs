"""The module catalog against I §9.3.1."""

from __future__ import annotations

from opencs import policy
from opencs.messages.catalog import CATALOG, is_module_index, module_level


def test_indices_are_the_five_level_groups() -> None:
    expected = [*range(1, 10), *range(11, 19), *range(21, 31), *range(31, 38), *range(41, 48)]
    assert sorted(CATALOG) == expected
    assert len(CATALOG) == 41


def test_level_follows_the_index() -> None:
    for index, module in CATALOG.items():
        assert module.index == index
        assert module.level == module_level(index)
    assert [module_level(i) for i in (1, 9, 11, 18, 21, 30, 31, 37, 41, 47)] == [
        1, 1, 2, 2, 3, 3, 4, 4, 5, 5,
    ]  # fmt: skip


def test_membership_boundaries() -> None:
    for gap in (0, 10, 19, 20, 38, 40, 48, 240):
        assert not is_module_index(gap)
    for edge in (1, 9, 11, 18, 21, 30, 31, 37, 41, 47):
        assert is_module_index(edge)


def test_known_rows() -> None:
    assert (CATALOG[5].name, CATALOG[5].weight, CATALOG[5].mode) == ("Rocket Pack", 4, 1)
    assert (CATALOG[26].name, CATALOG[26].weight) == ("Tri-Shot", 9)
    assert (CATALOG[36].name, CATALOG[36].level, CATALOG[36].mode) == ("Call Sentinel", 4, 1)
    assert (CATALOG[47].name, CATALOG[47].level) == ("Turret", 5)


def test_policy_bounds_match() -> None:
    assert min(CATALOG) == policy.MODULE_INDEX_LOW
    assert max(CATALOG) == policy.MODULE_INDEX_HIGH
