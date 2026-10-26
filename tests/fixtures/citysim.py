"""A simulated population living in one city for days of simulated time (the M11 long run).

Clients are sans-socket sessions driven by a ``ManualClock``: they arrive and leave on a daily
rhythm, walk, shoot, order, die, chat, and leave by a clean quit, a dropped line or by going
silent. Time moves in coarse steps and jumps while nobody is there. After every step the invariants
of ``fixtures.fuzzing`` are checked against what each client was sent.
"""

from __future__ import annotations

import logging
import random
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from fixtures.fuzzing import ClientModel, ErrorLog, check_city
from fixtures.pods import report
from fixtures.sessions import Harness, World, connect, make_world
from opencs import policy
from opencs.config import CityConfig
from opencs.messages import ClientMessage, WirePos, WireXZ, client, client_payload, server
from opencs.session.chamber import ChamberPhase
from opencs.session.ingame import InGamePhase
from opencs.wire.frame import build_frame
from opencs.world.city import City
from opencs.world.ids import IdRange

HOUR = 3600.0
DAY = 24 * HOUR


#: Arrivals per hour by hour of the day: a quiet night, a steady day, a busy evening.
def arrival_rate(hour: int) -> float:
    if hour < 6:
        return 0.5
    if hour < 17:
        return 6.0
    return 18.0


def frame(msg: ClientMessage) -> bytes:
    return build_frame(client_payload(msg), terminator=b"\x0a")


def size_of(root: object, *, skip: Iterable[object] = ()) -> int:
    """How many elements the containers reachable from ``root`` hold: a leak shows as growth. Each
    container counts its length; objects are followed through their attributes."""
    seen = {id(o) for o in skip}
    total = 0
    stack: list[Any] = [root]
    while stack:
        obj = stack.pop()
        if id(obj) in seen or isinstance(obj, str | bytes | int | float | bool | type(None)):
            continue
        seen.add(id(obj))
        if isinstance(obj, dict):
            total += len(obj)
            stack.extend(obj.keys())
            stack.extend(obj.values())
        elif isinstance(obj, list | set | frozenset | tuple | deque):
            total += len(obj)
            stack.extend(obj)
        elif hasattr(obj, "__dict__") or hasattr(type(obj), "__slots__"):
            if isinstance(obj, Harness | World | logging.Logger):
                continue  # the simulation's own state, not the server's
            stack.extend(getattr(obj, "__dict__", {}).values())
            for cls in type(obj).__mro__:
                for name in getattr(cls, "__slots__", ()):
                    if hasattr(obj, name):
                        stack.append(getattr(obj, name))
    return total


@dataclass
class SimClient:
    name: str
    harness: Harness
    model: ClientModel
    leaves_at: float
    leave_how: str  # "quit", "drop" or "silent"
    x: int = 300
    z: int = 300
    stage: str = "new"  # new, dropwait, flying, dead, quitting, gone
    legs: list[int] = field(default_factory=list)
    finish_at: float = 0.0


class CitySim:
    """One city, a population, and the checks."""

    def __init__(
        self,
        config: CityConfig,
        seed: int,
        *,
        id_ranges: dict[IdRange, tuple[int, int]] | None = None,
        rate_scale: float = 1.0,
        step: float = 10.0,
    ) -> None:
        self.rng = random.Random(seed)
        self.world = make_world(config, default=config.letter, seed=seed, id_ranges=id_ranges)
        self.city: City = self.world.services.cities[config.letter]
        self.letter = config.letter
        self.clients: list[SimClient] = []
        self.rate_scale = rate_scale
        self.step = step
        self.joined = 0
        self.refused = 0
        self.refusals: dict[str, int] = {}
        self.max_players = 0
        self.resets = 0
        self.resumed = 0
        self.messages = 0  # server messages checked to encode into a valid frame
        self.violations: list[str] = []
        self.size_by_hour: list[int] = []
        #: The sizes sampled while the city stood empty and reset: what it keeps between games.
        self.empty_sizes: list[int] = []
        self._was_in_game = False
        self._next_arrival = self._arrival_after(0.0)
        self.start = self.world.clock.now()

    # --- the population ------------------------------------------------------------------------

    def _arrival_after(self, t: float) -> float:
        """The next arrival after ``t`` (seconds into the run): a thinned Poisson process."""
        peak = 18.0 * self.rate_scale / HOUR
        while True:
            t += self.rng.expovariate(peak)
            hour = int(t // HOUR) % 24
            if self.rng.random() < arrival_rate(hour) * self.rate_scale / HOUR / peak:
                return t

    def _arrive(self) -> None:
        n = self.joined + self.refused
        account = self.rng.random() < 0.4
        name = f"ACC{self.rng.randrange(8)}" if account else f"G{self.rng.randrange(40)}"
        line = f"{name},secret" if account else policy.GUEST_ACCOUNT_NAME  # guests share JOINGENIE
        if any(c.name == name and c.stage != "gone" for c in self.clients):
            self.refused += 1
            return
        h = connect(self.world, address=f"10.{n // 250 % 250}.{n % 250}.1")
        h.feed(f"AT\r\nHHH\r\r{line}\r<Not Telling!>\rM 1380;3\r")
        h.feed("CSTTBT 105 150\r")
        key = f"acct:{name}" if account else name
        held = self.world.services.held.get(key) if account else None
        if held is not None and self.rng.random() < 0.7:
            h.feed("/RESUME\r")
            self.resumed += 1
        else:
            h.feed(f"/PLAY {self.letter} {policy.TEAM_NAMES[self.rng.randrange(4)]}\r")
        h.feed("/GO 0\r")
        h.io.raw.clear()
        if not isinstance(h.session.phase, InGamePhase) or h.io.closed:
            self.refused += 1
            for reason in h.io.closed_with:
                self.refusals[reason] = self.refusals.get(reason, 0) + 1
            h.session.closed()
            return
        self.joined += 1
        stay = min(max(self.rng.expovariate(1 / (25 * 60)), 60.0), 2 * HOUR)
        client_ = SimClient(
            name,
            h,
            ClientModel(h.io, name=name),
            self.world.clock.now() + stay,
            self.rng.choice(["quit", "quit", "drop", "drop", "silent"]),
        )
        self.clients.append(client_)
        self.max_players = max(self.max_players, len(self.city.players()))

    # --- one client's behavior ----------------------------------------------------------------

    def _pos(self, c: SimClient, y: int = 240) -> WirePos:
        return WirePos.from_world(c.x, y, c.z)

    def _act(self, c: SimClient, now: float) -> None:
        h, rng = c.harness, self.rng
        if c.stage == "new":
            h.feed(frame(report(4)))
            c.stage = "dropwait"
            return
        if c.stage == "dropwait":
            c.stage = "flying"
        if c.stage == "dead":
            h.feed(frame(client.DropMe()))
            c.stage = "dropwait"
            h.feed(frame(report(4)))
            return
        c.x = min(max(c.x + rng.randrange(-400, 401), 0), 4799)
        c.z = min(max(c.z + rng.randrange(-400, 401), 0), 4799)
        h.feed(frame(report(0, **_cells(c))))
        for leg in c.legs:
            h.feed(frame(client.LegArrived(leg)))
        c.legs.clear()
        for m in self._something_to_do(c):
            h.feed(frame(m))
        if rng.random() < 0.008:  # shot down
            h.feed(frame(report(2, **_cells(c))))
            h.feed(frame(client.Died()))
            c.stage = "dead"

    def _something_to_do(self, c: SimClient) -> list[ClientMessage]:
        """What a pilot does besides flying this step: most steps nothing much."""
        rng = self.rng
        known = [*self.city.live, *(p.id for p in self.city.players())]
        r = rng.random()
        msgs: list[ClientMessage] = []
        if r < 0.30 and c.model.self_id is not None:
            kind = rng.choice([1, 2, 3, 9])
            msgs.append(client.Shot(c.model.self_id, self._pos(c), 450, 0, kind))
        if r < 0.10 and known:
            msgs.append(client.HitReport(rng.choice(known), rng.choice([11, 21, 31, 91])))
        elif r < 0.14:
            msgs.append(client.TowerOrder(self._pos(c), rng.randrange(2), rng.randrange(1, 4)))
        elif r < 0.18:
            module = rng.choice([1, 2, 11, 21, 31, 41, 47])
            msgs.append(client.ModuleOrder(module, rng.randrange(1, 4), 1))
        elif r < 0.21 and known:
            msgs.append(client.PickupRequest(rng.choice(known)))
        elif r < 0.23 and known:
            msgs.append(client.EjectRequest(rng.choice(known)))
        elif r < 0.27:
            msgs.append(client.EffectRequest(rng.choice("RWHLMCSID")))
        elif r < 0.29:
            msgs.append(client.AirSupport(1, WireXZ.from_world(c.x, c.z)))
        elif r < 0.33:
            msgs.append(client.Chat(rng.randrange(2), b"hello city"))
        elif r < 0.35:
            msgs.append(
                client.Status(rng.randrange(200000), rng.randrange(1, 40), rng.randrange(5000))
            )
        elif r < 0.37:
            msgs.append(client.Grenade(self._pos(c), 100, 50))
        return msgs

    def _answer_legs(self, c: SimClient, messages: list[Any]) -> None:
        for m in messages:
            if isinstance(m, server.MobileCreate | server.MobileWarp):
                c.legs.append(m.id)
            elif isinstance(m, server.SendMuncher):
                c.legs.append(m.muncher)

    def _depart(self, c: SimClient, now: float) -> None:
        h = c.harness
        if c.leave_how == "drop":
            h.session.closed()
            c.stage = "gone"
        elif c.leave_how == "quit":
            h.feed(frame(client.Quit()))
            h.feed(b"QUIT\rQUIT\r")
            c.stage = "quitting"
            c.finish_at = now + policy.RETURN_SETTLE + 2
        else:  # silent: it sends nothing; the server notices
            c.stage = "silent"

    # --- the clock -----------------------------------------------------------------------------

    def run(self, hours: float, check_every: int = 1) -> None:
        clock = self.world.clock
        end = self.start + hours * HOUR
        steps = 0
        with ErrorLog() as errors:
            while clock.now() < end:
                steps += 1
                active = [c for c in self.clients if c.stage != "gone"]
                run_t = clock.now() - self.start
                idle_jump = min(max(self._next_arrival - run_t, 1.0), 600.0)
                dt = self.step if active else idle_jump
                clock.advance(dt)
                now = clock.now()
                self._tick(now)
                while self.start + self._next_arrival <= now:
                    self._arrive()
                    self._next_arrival = self._arrival_after(self._next_arrival)
                self._clients_step(now)
                if steps % check_every == 0:
                    self._check(errors, steps)
                hour = int((now - self.start) // HOUR)
                if hour >= len(self.size_by_hour):
                    self.size_by_hour.append(self.size())
                    if not self.city.in_game:
                        self.empty_sizes.append(self.size_by_hour[-1])

    def _tick(self, now: float) -> None:
        self.world.services.router.deliver(self.city, self.city.tick(now))
        self.world.services.persistence.flush(self.city)

    def _clients_step(self, now: float) -> None:
        for c in list(self.clients):
            h = c.harness
            if c.stage == "gone":
                self.clients.remove(c)
                continue
            if h.io.closed and c.stage != "quitting":  # the server closed it: the socket ends
                h.session.closed()
                c.stage = "gone"
                continue
            if c.stage == "silent":
                h.session.tick(now)
                continue
            h.session.tick(now)
            if c.stage == "quitting":
                if isinstance(h.session.phase, ChamberPhase) or now >= c.finish_at:
                    h.session.closed()
                    c.stage = "gone"
                h.io.raw.clear()
                continue
            if now >= c.leaves_at:
                self._depart(c, now)
                continue
            self._act(c, now)
            h.io.raw.clear()

    def _check(self, errors: ErrorLog, step: int) -> None:
        now = self.world.clock.now()
        problems = check_city(self.city)
        for c in self.clients:
            messages = c.model.absorb(consume=True)
            self.messages += len(messages)
            self._answer_legs(c, messages)
            problems += c.model.violations
            c.model.violations.clear()
        held = self.world.services.held.slots(self.letter)
        if not self.city.players():
            # S13: an empty city is reset unless a game is held, and the tick sees to it.
            if self.city.in_game != bool(held):
                problems.append(f"empty city: in_game={self.city.in_game} held={held}")
            if not self.city.in_game and self.city.live:
                problems.append("a reset city still holds entities")
        if self._was_in_game and not self.city.in_game:
            self.resets += 1
        self._was_in_game = self.city.in_game
        if errors.records:
            problems.append("logged errors:\n" + errors.describe())
        if problems:
            raise AssertionError(f"t={now - self.start:.0f}s step {step}: {problems}")

    def size(self) -> int:
        services = self.world.services
        return size_of(
            [
                self.city,
                services.router,
                services.identity,
                services.lobby,
                services.held,
                services.persistence,
                services.login_limits,
            ]
        )


def _cells(c: SimClient) -> dict[str, int]:
    pos = WirePos.from_world(c.x, 240, c.z)
    return {"cell_x": pos.cell_x, "cell_z": pos.cell_z, "x_off": pos.x_off, "z_off": pos.z_off}
