"""The lobby: who is connected, where they are, and which city and team a ``/PLAY`` lands in.

II §6 (cities, "Teams (S9)", capacity). Pure state and rules: no I/O, no time, no sockets. A
member is reached through the ``deliver`` callback it joined with, which the session layer supplies.
Team choice is lobby logic (S9); a pod's index within its team is the world's (M4).
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from opencs import policy
from opencs.config import CityConfig
from opencs.lobby import replies
from opencs.wire.text import sanitize_relay


@dataclass(eq=False, slots=True)
class Member:
    """A player in the Chamber or in a city. ``city`` is None while in the Chamber."""

    callsign: str
    account: str | None
    deliver: Callable[[bytes], None] = field(repr=False)
    city: str | None = None
    team: int | None = None

    @property
    def in_chamber(self) -> bool:
        return self.city is None


@dataclass(frozen=True, slots=True)
class Placement:
    """Where a ``/PLAY`` would put a player. ``note`` is a line to show when the request was not
    honored as asked (the team was full, or the city picks teams itself)."""

    city: CityConfig
    team: int
    note: str = ""


@dataclass(frozen=True, slots=True)
class Denied:
    """A ``/PLAY`` that cannot be satisfied, with the reason to show."""

    reason: str


class Lobby:
    """Members, cities and team assignment."""

    def __init__(self, cities: Iterable[CityConfig], default_city: str, rng: random.Random) -> None:
        self._cities = {c.letter: c for c in cities if c.enabled}
        self._default = default_city
        self._rng = rng
        self._members: list[Member] = []

    # --- members ---------------------------------------------------------------------------

    def join(self, callsign: str, account: str | None, deliver: Callable[[bytes], None]) -> Member:
        """Add a player, in the Chamber."""
        member = Member(callsign, account, deliver)
        self._members.append(member)
        return member

    def leave(self, member: Member) -> None:
        """Remove a player (it disconnected, or left the Chamber for the GEnie prompt)."""
        if member in self._members:
            self._members.remove(member)

    def members(self) -> list[Member]:
        """Everyone connected, by callsign."""
        return sorted(self._members, key=lambda m: m.callsign)

    def find(self, callsign: str) -> Member | None:
        """The member with this (upper-case) callsign."""
        return next((m for m in self._members if m.callsign == callsign), None)

    def chamber(self) -> list[Member]:
        """Members in the Chamber."""
        return [m for m in self._members if m.in_chamber]

    # --- cities ----------------------------------------------------------------------------

    def cities(self) -> list[CityConfig]:
        """The enabled cities by letter."""
        return [self._cities[k] for k in sorted(self._cities)]

    def city(self, letter: str) -> CityConfig | None:
        """The enabled city with this letter, or None."""
        return self._cities.get(letter)

    def in_city(self, letter: str) -> list[Member]:
        """Members of a city, by team then callsign."""
        found = [m for m in self._members if m.city == letter]
        return sorted(found, key=lambda m: (m.team if m.team is not None else 0, m.callsign))

    def team_members(self, letter: str, team: int) -> list[Member]:
        """Members of one team of a city, by callsign."""
        return sorted((m for m in self._members if m.city == letter and m.team == team), key=_call)

    def count(self, letter: str) -> int:
        """Players in a city."""
        return sum(1 for m in self._members if m.city == letter)

    def _team_count(self, letter: str, team: int) -> int:
        return sum(1 for m in self._members if m.city == letter and m.team == team)

    def _open_teams(self, city: CityConfig) -> list[int]:
        """Teams with room, or none if the city is at capacity (II §6, S9)."""
        if self.count(city.letter) >= city.capacity:
            return []
        return [
            t
            for t in range(policy.TEAM_COUNT)
            if self._team_count(city.letter, t) < policy.MAX_PLAYERS_PER_TEAM
        ]

    def has_room(self, city: CityConfig) -> bool:
        return bool(self._open_teams(city))

    # --- /PLAY -----------------------------------------------------------------------------

    def place(self, letter: str | None, team: int | None) -> Placement | Denied:
        """Choose the city and team for a ``/PLAY`` (II §4.2).

        With no letter: the most populated city with room; ties, and all-empty, go to the default
        city when it is among them. With a letter: that city if enabled and not full.
        """
        if letter is None:
            return self._place_anywhere(team)
        city = self.city(letter)
        if city is None:
            return Denied(f"NO SUCH CITY: {letter}")
        if not self.has_room(city):
            return Denied(f"CITY {letter} IS FULL")
        return self._place_in(city, team)

    def _place_anywhere(self, team: int | None) -> Placement | Denied:
        roomy = [c for c in self.cities() if self.has_room(c)]
        if not roomy:
            return Denied("ALL CITIES ARE FULL")
        busiest = max(self.count(c.letter) for c in roomy)
        tied = [c for c in roomy if self.count(c.letter) == busiest]
        city = next((c for c in tied if c.letter == self._default), tied[0])
        return self._place_in(city, team)

    def _place_in(self, city: CityConfig, requested: int | None) -> Placement:
        open_teams = self._open_teams(city)
        note = ""
        if city.team_policy == "request-or-balance" and requested in open_teams:
            assert requested is not None
            return Placement(city, requested)
        if requested is not None:
            if city.team_policy == "request-or-balance":
                note = f"{policy.TEAM_NAMES[requested]} IS FULL"
            else:
                note = f"CITY {city.letter} CHOOSES TEAMS ITSELF"
        # The balancer's teams, or any open team if those are all full (II §6).
        candidates = [t for t in city.balance_teams if t in open_teams] or open_teams
        if city.team_policy == "random":
            chosen = self._rng.choice(candidates)
        else:
            chosen = min(candidates, key=lambda t: (self._team_count(city.letter, t), t))
        if note:
            note += f": YOU ARE ON {policy.TEAM_NAMES[chosen]}"
        return Placement(city, chosen, note)

    def enter(self, member: Member, placement: Placement) -> bool:
        """Put ``member`` in the placed city and team; False if the place filled meanwhile (the
        launch takes up to ``GO_WAIT`` seconds, during which others may join)."""
        if placement.team not in self._open_teams(placement.city):
            return False
        member.city = placement.city.letter
        member.team = placement.team
        return True

    def return_to_chamber(self, member: Member) -> None:
        """``member`` is back from a game: in the Chamber, in no city or team."""
        member.city = None
        member.team = None

    # --- chat ------------------------------------------------------------------------------

    def relay_chat(self, sender: Member, text: str) -> int:
        """Send ``CALLSIGN: text`` to every other member in the Chamber, never to the sender nor
        to players in a game (II §4.3). Returns how many received it.

        The text is stripped to ``0x20..0x7e`` and cut to ``CHAT_MAX_CHARS``: a ``0x01`` or
        ``0x05`` would arm command mode on the receiver (II §4.3).
        """
        clean = sanitize_relay(text.encode("latin-1", "ignore"))[: policy.CHAT_MAX_CHARS]
        if not clean.strip():
            return 0
        data = replies.chat_line(sender.callsign, clean.decode("ascii"))
        others = [m for m in self._members if m.in_chamber and m is not sender]
        for member in others:
            member.deliver(data)
        return len(others)


def _call(member: Member) -> str:
    return member.callsign
