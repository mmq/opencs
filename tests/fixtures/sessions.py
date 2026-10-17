"""A recording ``SessionIO`` and a session harness driven by a ``ManualClock``."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

from fixtures.lobbies import city
from opencs import policy
from opencs.clock import ManualClock
from opencs.config import CityConfig
from opencs.connlog import ConnectionContext, ConnectionLog
from opencs.lobby.identity import Identity
from opencs.lobby.lobby import Lobby
from opencs.messages import ServerMessage
from opencs.routing import Router
from opencs.session.held import HeldGames
from opencs.session.persistence import Persistence
from opencs.session.phases import Services
from opencs.session.session import Session
from opencs.store.base import ProgressStore
from opencs.store.memory import MemoryAccountStore, MemoryProgressStore
from opencs.world.city import City

FAST_ITERATIONS = 1000


@dataclass
class FakeIO:
    """Records what a session sends; ``close`` is recorded, not acted on."""

    raw: list[bytes] = field(default_factory=list)
    messages: list[ServerMessage] = field(default_factory=list)
    closed_with: list[str] = field(default_factory=list)

    def send_raw(self, data: bytes) -> None:
        self.raw.append(data)

    def send(self, msg: ServerMessage) -> None:
        self.messages.append(msg)

    def send_first(self, msg: ServerMessage) -> None:
        self.messages.insert(0, msg)

    def close(self, reason: str) -> None:
        self.closed_with.append(reason)

    def take(self) -> bytes:
        """Everything sent since the last ``take``."""
        data = b"".join(self.raw)
        self.raw.clear()
        return data

    @property
    def closed(self) -> bool:
        return bool(self.closed_with)


@dataclass
class World:
    """Shared services for one or more sessions."""

    clock: ManualClock
    identity: Identity
    lobby: Lobby
    services: Services
    progress: ProgressStore
    sessions: list[Harness] = field(default_factory=list)


@dataclass
class Harness:
    """One session with its fake IO and the shared world."""

    session: Session
    io: FakeIO
    world: World

    @property
    def clock(self) -> ManualClock:
        return self.world.clock

    def feed(self, data: bytes | str) -> None:
        self.session.feed(data.encode("latin-1") if isinstance(data, str) else data)

    def advance(self, seconds: float) -> None:
        """Move time on and let the session notice, as the connection's ticker does."""
        self.clock.advance(seconds)
        self.session.tick(self.clock.now())

    def take(self) -> bytes:
        return self.io.take()


def make_world(
    *cities: CityConfig,
    default: str = "B",
    seed: int = 1,
    start: float = 100.0,
    progress: ProgressStore | None = None,
) -> World:
    clock = ManualClock(start)
    identity = Identity(MemoryAccountStore(), iterations=FAST_ITERATIONS)
    configs = cities or (city("A"), city("B"), city("C"))
    held = HeldGames(clock)
    lobby = Lobby(configs, default, random.Random(seed), held)
    cities_by_letter = {c.letter: City(c, reservations=held) for c in configs}
    router = Router()
    progress = progress or MemoryProgressStore()
    services = Services(
        identity, lobby, clock, cities_by_letter, router, Persistence(progress, clock), held
    )
    return World(clock, identity, lobby, services, progress)


def connect(world: World, conn_id: int | None = None) -> Harness:
    io = FakeIO()
    n = conn_id if conn_id is not None else len(world.sessions) + 1
    log = ConnectionLog(logging.getLogger("test.session"), ConnectionContext(n))
    harness = Harness(Session(io, world.services, log), io, world)
    world.sessions.append(harness)
    return harness


LOGIN = "HHH\rJOINGENIE\r<Not Telling!>\rM 1380;3\r"


def login(world: World, account: str = "JOINGENIE", *, enter: bool = True) -> Harness:
    """A session taken through the logon to the Chamber's version check (or to the ``*``)."""
    h = connect(world)
    h.feed(f"AT\r\nHHH\r\r{account}\r<Not Telling!>\r")
    if enter:
        h.feed("M 1380;3\r")
    return h


def enter_lobby(world: World, account: str = "JOINGENIE") -> Harness:
    """A session that is in the Chamber, version check answered, output cleared."""
    h = login(world, account)
    h.feed("CSTTBT 105 150\r")
    h.take()
    return h


def play(world: World, letter: str = "A", team: int = 0, account: str = "JOINGENIE") -> Harness:
    """A session in the Chamber taken through ``/PLAY`` and ``/GO 0`` into the in-game phase."""
    h = enter_lobby(world, account)
    h.feed(f"/PLAY {letter} {policy.TEAM_NAMES[team]}\r")
    h.feed("/GO 0\r")
    h.take()
    return h
