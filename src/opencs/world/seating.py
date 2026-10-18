"""Seating: who may join a city and what a seat is made of (I §6.2, I §7.2, I §7).

Split from ``City`` so the city keeps to the world's state and time. ``seat`` decides whether there
is room and builds the ``Player`` and its ``'B'``; the city then registers the player, starts the
game and notes the record.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from opencs import policy
from opencs.config import CityConfig
from opencs.messages import server
from opencs.world.ids import IdRange, IdSpace
from opencs.world.player import Player
from opencs.world.progress import SavedProgress
from opencs.world.resume import ResumeRecord
from opencs.world.view import ClientView
from opencs.world.weather import CLIENT_CONDITION, WEATHER_KEY

log = logging.getLogger("opencs.world")


@dataclass(frozen=True, slots=True)
class JoinResult:
    """A player joined. ``hello`` is the ``'B'`` to send it (I §7.1)."""

    player_id: int
    team: int
    index: int
    hello: server.Session


@dataclass(frozen=True, slots=True)
class JoinRefused:
    """A join that cannot be honored."""

    reason: str


@dataclass(frozen=True, slots=True)
class Applicant:
    """Who is asking for a seat and what it brings (``City.join`` documents the fields)."""

    callsign: str
    team: int
    identity: str | None
    saved: SavedProgress | None
    resume: ResumeRecord | None


def seat(
    config: CityConfig,
    players: Mapping[int, Player],
    held_teams: list[int],
    ids: IdSpace,
    earliest_start: float | None,
    holds: Callable[[int], bool],
    applicant: Applicant,
    now: float,
) -> tuple[Player, server.Session] | JoinRefused:
    """Room, an id, the lowest free index in the team (I §6.3) and the ``'B'``; the player is
    not yet in the roster. ``held_teams`` are the teams of the slots held for others."""
    callsign, team = applicant.callsign, applicant.team
    if len(players) + len(held_teams) >= config.capacity:
        return JoinRefused(f"CITY {config.letter} IS FULL")
    taken = {p.index for p in players.values() if p.team == team}
    free = [i for i in range(policy.MAX_PLAYERS_PER_TEAM) if i not in taken]
    if not free or len(taken) + held_teams.count(team) >= policy.MAX_PLAYERS_PER_TEAM:
        return JoinRefused(f"{policy.TEAM_NAMES[team]} IS FULL")
    player_id = ids.allocate(IdRange.PLAYER, earliest_start, holds)
    if player_id is None:
        # Inherent in I §6.2, II §8: a client that stays long enough has been introduced to every id
        # in the range and its registry never unlinks one, so it holds the range until it leaves.
        longest = sorted(players.values(), key=lambda p: p.view.joined_at)[:3]
        log.warning(
            "city %s: %s refused, no free player id: the range is held by connected clients, "
            "longest-connected first: %s",
            config.letter,
            callsign,
            ", ".join(
                f"{p.callsign} (id {p.id}, joined at {p.view.joined_at:.0f})" for p in longest
            ),
        )
        return JoinRefused("NO FREE PLAYER ID")
    index = free[0]
    try:
        # Build the 'B' before seating anyone: if it cannot be built, nobody is seated.
        hello = server.Session(
            self_id=player_id,
            index=index,
            team=team,
            callsign=callsign.encode("ascii"),
            network=policy.NETWORK_NAME.encode("ascii"),
            city=config.name.encode("ascii"),
            max_mult=config.max_multiplier,
            tower_output=config.tower_output,
            max_module_level=config.max_module_level,
        )
    except Exception:
        ids.release(IdRange.PLAYER, player_id, now)
        raise
    player = Player(
        player_id,
        callsign,
        team,
        index,
        ClientView(player_id, now),
        identity=applicant.identity,
        saved=applicant.saved,
        resume=applicant.resume if applicant.identity is not None else None,
    )
    if player.resume is not None:
        player.score.multiplier = min(player.score.multiplier, config.max_multiplier)
    player.view.hold(player_id)  # the client creates its own pod from 'B' (I §7.1)
    # A fresh client already shows its own default palette (I §8.20), which is a client fact,
    # not the city's starting condition: it is sent one only once the weather differs from it.
    player.view.tell(WEATHER_KEY, CLIENT_CONDITION)
    return player, hello
