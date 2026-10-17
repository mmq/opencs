"""The judge's ``--checks`` syntax (tools/judge.py)."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "judge", Path(__file__).parents[2] / "tools" / "judge.py"
)
assert SPEC is not None and SPEC.loader is not None
judge = importlib.util.module_from_spec(SPEC)
sys.modules["judge"] = judge  # dataclasses look their module up here
SPEC.loader.exec_module(judge)


def test_lists_ranges_and_mixtures() -> None:
    assert judge.parse_checks("1,2,5") == [1, 2, 5]
    assert judge.parse_checks("1-3") == [1, 2, 3]
    assert judge.parse_checks("1-14,17-19") == [*range(1, 15), 17, 18, 19]
    assert judge.parse_checks("5,3-4,4") == [3, 4, 5]
    assert judge.parse_checks("19-19") == [19]


@pytest.mark.parametrize("text", ["", "0", "20", "1-20", "0-3", "5-3", "a", "1-", "-3", "1--3"])
def test_bad_checks_are_refused(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        judge.parse_checks(text)
