"""Lobby builders shared by the lobby, replies and session tests."""

from __future__ import annotations

import random

from opencs.config import CityConfig
from opencs.lobby.lobby import Lobby, Member


def city(letter: str, **kw: object) -> CityConfig:
    fields: dict[str, object] = {
        "letter": letter,
        "name": f"CITY {letter}",
        "map": ord(letter) - ord("A") + 1,
        "max_multiplier": 8,
        "tower_output": 1,
        "max_module_level": 2,
        "capacity": 16,
        "team_policy": "request-or-balance",
        "balance_teams": (0, 1, 2, 3),
        "banned_modules": (),
        "enabled": True,
    }
    fields.update(kw)
    return CityConfig(**fields)  # type: ignore[arg-type]


def make(*cities: CityConfig, default: str = "B", seed: int = 1) -> Lobby:
    return Lobby(cities or (city("A"), city("B"), city("C")), default, random.Random(seed))


def add(
    lobby: Lobby,
    callsign: str,
    letter: str | None = None,
    team: int | None = None,
    *,
    account: str | None = None,
) -> Member:
    member = lobby.join(callsign, account, lambda data: None)
    if letter is not None:
        assert team is not None
        member.city, member.team = letter, team
    return member


def fill(lobby: Lobby, letter: str, n: int, prefix: str = "P") -> None:
    """Put ``n`` players in a city, spread over the teams (at most 4 each)."""
    for i in range(n):
        add(lobby, f"{prefix}{letter}{i}", letter, i % 4)
