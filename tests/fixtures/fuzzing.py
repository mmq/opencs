"""Shared pieces of the fuzz suites and the long run: random client messages and the invariants a
city must keep for every client (DESIGN §5)."""

from __future__ import annotations

import inspect
import logging
import random
import types
import typing
from collections.abc import Sequence
from dataclasses import dataclass, field, fields

from fixtures.sessions import FakeIO
from opencs.messages import ClientMessage, ServerMessage, WirePos, WireXZ, client, server
from opencs.messages.base import server_payload
from opencs.messages.fields import is_safe_kind
from opencs.wire.codec import BYTE_MIN, ENC_NUM_MAX
from opencs.wire.frame import build_frame
from opencs.world.city import City

CLIENT_TYPES: list[type[ClientMessage]] = [
    cls
    for _, cls in inspect.getmembers(client, inspect.isclass)
    if issubclass(cls, ClientMessage) and cls is not ClientMessage and hasattr(cls, "TYPE")
]

#: Values that name something real, something next to it, or nothing: pod ids, guppy, Muncher,
#: Sentinel and structure ranges (I §6.2, II §8), small numbers, and the largest an ``enc_num``
#: holds.
INTERESTING = [
    0, 1, 2, 3, 4, 5, 9, 10, 15, 16, 31, 32, 100, 150, 239, 240, 250, 251, 259, 260, 261, 4095,
    4096, 4097, 8191, 8192, 8193, 32767, 32768, 65535, 1_000_000, ENC_NUM_MAX - 1, ENC_NUM_MAX,
]  # fmt: skip


#: Fields where real values are few: the fuzzer draws these 70% of the time, so the rules behind
#: them are reached, and the rest of the time anything.
LIKELY: dict[tuple[str, str], list[int]] = {
    ("PodState", "state"): [0, 0, 0, 0, 1, 2, 3, 4],
    ("PodState", "weight"): [0, 0, 5, 20, 100],
    ("Shot", "kind"): [1, 2, 3, 9, 11, 12, 13, 19, 7],
    ("TowerOrder", "mega"): [0, 0, 1],
    ("TowerOrder", "priority"): [0, 1, 2, 3],
    ("ModuleOrder", "module"): [1, 2, 3, 11, 12, 21, 30, 31, 37, 41, 47, 0, 48],
    ("ModuleOrder", "level"): [0, 1, 2, 3, 6],
    ("ModuleOrder", "priority"): [0, 1, 2, 3],
    ("Chat", "style"): [0, 0, 1, 2],
    ("HitReport", "damage"): [11, 21, 31, 91, 15, 0, 100],
    ("Status", "multiplier"): [1, 2, 3, 8, 20],
}


class UnmakeableError(Exception):
    """The generator could not build a valid message of this class (a field range is too tight)."""


def _value(rng: random.Random, hint: object, known: Sequence[int] = ()) -> object:  # noqa: PLR0911
    if hint is int:
        r = rng.random()
        if known and r < 0.35:
            return rng.choice(known)  # an id that names something live right now
        if r < 0.5:
            return rng.choice(INTERESTING)
        if r < 0.8:
            return rng.randrange(0, 240)
        return rng.randrange(0, 70000)
    if hint is bytes:
        return bytes(rng.randrange(0x20, 0x7F) for _ in range(rng.randrange(0, 90)))
    if hint is str:
        return rng.choice("RWHLMCSIDA")
    if hint is WirePos:
        return WirePos(
            rng.randrange(0, 15),
            rng.randrange(0, 16),
            rng.choice([0, 1, 75, 299, 300, 599, 600, 1000, 4000, rng.randrange(0, 5000)]),
            rng.choice([0, 72, 240, 1000, 5000, 50000, rng.randrange(0, 100000)]),
            rng.choice([0, 1, 75, 299, 300, 599, 600, 1000, 4000, rng.randrange(0, 5000)]),
        )
    if hint is WireXZ:
        return WireXZ(
            rng.randrange(0, 15),
            rng.randrange(0, 16),
            rng.randrange(0, 600),
            rng.randrange(0, 600),
        )
    origin = typing.get_origin(hint)
    if origin is tuple:
        return tuple(_value(rng, int) for _ in range(9))
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(hint)]
        return (
            _value(rng, rng.choice(args), known)
            if type(None) not in args or rng.random() < 0.7
            else None
        )
    if hint is type(None):
        return None
    raise UnmakeableError(f"no generator for {hint!r}")


def random_client_message(
    rng: random.Random,
    kinds: Sequence[type[ClientMessage]] | None = None,
    known: Sequence[int] = (),
) -> ClientMessage:
    """A valid, encodable client message of a random type with random field values (ids naming
    nothing, huge coordinates). Values a field cannot hold are drawn again."""
    for _ in range(200):
        cls = rng.choice(kinds or CLIENT_TYPES)
        hints = typing.get_type_hints(cls, include_extras=False)
        try:
            kwargs = {}
            for f in fields(cls):  # type: ignore[arg-type]
                likely = LIKELY.get((cls.__name__, f.name))
                if likely and rng.random() < 0.7:
                    kwargs[f.name] = rng.choice(likely)
                else:
                    kwargs[f.name] = _value(rng, hints[f.name], known)
            msg = cls(**kwargs)
            msg.body()  # encodable
            return msg
        except (ValueError, UnmakeableError):
            continue
    raise UnmakeableError("no valid message in 200 draws")


class ErrorLog(logging.Handler):
    """Collects every ERROR record: an exception the session or the city had to catch is a bug the
    fuzzer must report, and containment would otherwise hide it."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def __enter__(self) -> ErrorLog:
        logging.getLogger().addHandler(self)
        return self

    def __exit__(self, *exc: object) -> None:
        logging.getLogger().removeHandler(self)

    def describe(self) -> str:
        out = []
        for r in self.records[:3]:
            text = self.format(r) if r.exc_info is None else logging.Formatter().format(r)
            out.append(text)
        return "\n".join(out)


@dataclass
class ClientModel:
    """What one client has been sent, replayed from the messages its session emitted, to check the
    server's promises about entity ids (I §6.2, I §7.2, I §7.3)."""

    io: FakeIO
    name: str = "?"
    self_id: int | None = None
    held: set[int] = field(default_factory=set)
    seen: set[int] = field(default_factory=set)
    violations: list[str] = field(default_factory=list)

    def absorb(self) -> None:
        """Check every message emitted since the last call."""
        # By identity: a ping reply is inserted ahead of the queue (``send_first``).
        pending = [m for m in self.io.messages if id(m) not in self.seen]
        for msg in sorted(pending, key=lambda m: isinstance(m, server.PingReply)):
            self.seen.add(id(msg))
            self._check(msg)

    def _check(self, msg: ServerMessage) -> None:
        build_frame(server_payload(msg, BYTE_MIN))  # encodes into a valid frame
        if isinstance(msg, server.Session):
            if self.self_id is None:  # the 'B' is repeated until the first frame
                self.self_id = msg.self_id
                self.held = {msg.self_id}
        elif isinstance(msg, server.Introduce):
            if msg.id in self.held:
                self.violations.append(f"{self.name}: 'G' twice for id {msg.id}")
            self.held.add(msg.id)
        elif isinstance(msg, server.Delete):
            if msg.id not in self.held:
                self.violations.append(f"{self.name}: 'H' for id {msg.id} it does not hold")
            self.held.discard(msg.id)
        elif isinstance(msg, server.PodState):
            if msg.state not in (0, 1):
                self.violations.append(f"{self.name}: 'b' state {msg.state}")
            if msg.id not in self.held and msg.id != self.self_id:
                self.violations.append(f"{self.name}: 'b' about id {msg.id} it does not hold")
        elif isinstance(msg, server.Shot) and not is_safe_kind(msg.kind):
            self.violations.append(f"{self.name}: 'c' kind {msg.kind}")


def check_city(city: City) -> list[str]:
    """Violations of the city's own invariants right now."""
    problems: list[str] = []
    players = city.players()
    slots = [(p.team, p.index) for p in players]
    if len(set(slots)) != len(slots):
        problems.append(f"duplicate (team, index): {slots}")
    ids = [p.id for p in players]
    if len(set(ids)) != len(ids):
        problems.append(f"duplicate player ids {ids}")
    known = set(city.live) | set(ids)
    for p in players:
        stale = p.view.held - known
        if stale:
            problems.append(f"player {p.id} holds {sorted(stale)} which are not live")
    return problems
