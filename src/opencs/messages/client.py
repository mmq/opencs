"""Client to server messages, one class per row of I §8.

The client is hostile input: decoding checks only that the bytes can be read and re-encoded
(DESIGN §5); sessions decide what odd values mean. Names that exist on both sides (``PodState``,
``Shot``, ``Chat``) live in separate modules: ``client.PodState`` and ``server.PodState``.
C to S frames never carry a tag, whatever the case of the type (SPEC A, conventions).
"""

from __future__ import annotations

from typing import ClassVar, Self

from opencs.messages.base import ClientMessage, DecodeError, message, sub_letters
from opencs.messages.fields import (
    WirePos,
    WireXZ,
    is_safe_kind,
    read_b10,
    read_b88,
    reduced_kind,
)
from opencs.wire.codec import Reader, Writer, bias10_encode

#: One-shot effects: sub letter to the item the client used (I §8.7, I §9.3.5, II §16.1).
EFFECTS = {
    "R": "Repair",
    "W": "Weather",
    "H": "Harden",
    "L": "Liberator",
    "M": "Mine",
    "C": "Call Muncher",
    "S": "Call Sentinel",
    "I": "Immunize",
    "D": "Mine Sweep",
}

#: Bays a pod has (I §8.6).
BAY_COUNT = 9


@message
class DropMe(ClientMessage):
    """``'A'`` (nothing): "drop me", sent after each death's fly-over only (I §8.3)."""

    TYPE: ClassVar[str] = "A"

    def body(self) -> bytes:
        return b""

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls()


@message
class Quit(ClientMessage):
    """``'C'`` (nothing): quit request, answered with ``server.QuitAck`` (I §8.5)."""

    TYPE: ClassVar[str] = "C"

    def body(self) -> bytes:
        return b""

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls()


@message
class Status(ClientMessage):
    """``'D'`` ``'S'`` ``score:num, multiplier:b10, bay_points:num``: the client's status, every few
    seconds (I §8.6)."""

    TYPE: ClassVar[str] = "D"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("S")
    score: int
    multiplier: int
    bay_points: int

    def body(self) -> bytes:
        return Writer().raw(b"S").num(self.score).b10(self.multiplier).num(self.bay_points).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num(), read_b10(r), r.num())


@message
class Bays(ClientMessage):
    """``'D'`` ``'B'`` ``level x9:b10, marker:b88``: after a bay change (I §8.6)."""

    TYPE: ClassVar[str] = "D"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("B")
    levels: tuple[int, ...]
    marker: int

    def body(self) -> bytes:
        w = Writer().raw(b"B")
        for level in self.levels:
            w.b10(level)
        return w.b88(self.marker).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        levels = tuple(read_b10(r) for _ in range(BAY_COUNT))
        return cls(levels, read_b88(r))


@message
class EffectRequest(ClientMessage):
    """``'E'`` ``<R W H L M C S I D>`` and nothing more: a one-shot effect was used (I §8.7).
    ``effect`` is the sub letter; ``EFFECTS`` names them."""

    TYPE: ClassVar[str] = "E"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("".join(EFFECTS))
    effect: str

    def body(self) -> bytes:
        return self.effect.encode("ascii")

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(chr(sub))


@message
class AirSupport(ClientMessage):
    """``'E'`` ``'A'`` ``priority:b10, grid, Xoff, Zoff``: Air Support at that point (I §8.7)."""

    TYPE: ClassVar[str] = "E"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("A")
    priority: int
    pos: WireXZ

    def body(self) -> bytes:
        w = Writer().raw(b"A").b10(self.priority)
        self.pos.write(w)
        return w.bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(read_b10(r), WireXZ.read(r))


@message
class Grenade(ClientMessage):
    """``'E'`` ``'G'`` ``grid, Xoff, Y, Zoff, yaw:num, pitch:num``: a grenade fired; relay to every
    other client (I §8.7)."""

    TYPE: ClassVar[str] = "E"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("G")
    pos: WirePos
    yaw: int
    pitch: int

    def body(self) -> bytes:
        w = Writer().raw(b"G")
        self.pos.write(w)
        return w.num(self.yaw).num(self.pitch).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(WirePos.read(r), r.num(), r.num())


@message
class Ping(ClientMessage):
    """``'E'`` ``'N'``: latency ping; answer at once with ``server.PingReply`` (I §8.7)."""

    TYPE: ClassVar[str] = "E"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("N")

    def body(self) -> bytes:
        return b"N"

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls()


@message
class ModuleOrder(ClientMessage):
    """``'E'`` ``'P'`` ``module:b10, level:b10, priority:b10``: a module purchase order (I §8.7)."""

    TYPE: ClassVar[str] = "E"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("P")
    module: int
    level: int
    priority: int

    def body(self) -> bytes:
        return Writer().raw(b"P").b10(self.module).b10(self.level).b10(self.priority).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(read_b10(r), read_b10(r), read_b10(r))


@message
class EchoReply(ClientMessage):
    """``'E'`` ``'c'`` ``value:b10, word:num``: the answer to a ``server.EchoProbe`` (I §8.7)."""

    TYPE: ClassVar[str] = "E"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("c")
    value: int
    word: int

    def body(self) -> bytes:
        return Writer().raw(b"c").b10(self.value).num(self.word).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(read_b10(r), r.num())


@message
class Drone(ClientMessage):
    """``'E'`` ``'d'`` ``'O'`` (Drone view off, ``position`` None) or ``'d'`` ``'M'`` ``grid,
    Xoff, Zoff`` (Drone position); no reply (I §8.7)."""

    TYPE: ClassVar[str] = "E"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("d")
    position: WireXZ | None = None

    def body(self) -> bytes:
        if self.position is None:
            return b"dO"
        w = Writer().raw(b"dM")
        self.position.write(w)
        return w.bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        form = r.raw(1)
        if form == b"O":
            return cls(None)
        if form == b"M":
            return cls(WireXZ.read(r))
        raise DecodeError(f"'E' 'd' form {form!r} is neither 'O' nor 'M'")


@message
class HitReport(ClientMessage):
    """``'I'`` ``target:num, damage:b10``: a hit this client's simulation registered on ``target``;
    see I §8.11 before trusting it. ``damage = magnitude * 10 + weapon_class``."""

    TYPE: ClassVar[str] = "I"
    target: int
    damage: int

    @property
    def weapon_class(self) -> int:
        """The weapon class (damage byte modulo 10)."""
        return self.damage % 10

    @property
    def magnitude(self) -> int:
        """The magnitude (damage byte divided by 10)."""
        return self.damage // 10

    def body(self) -> bytes:
        return Writer().num(self.target).b10(self.damage).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num(), read_b10(r))


@message
class Died(ClientMessage):
    """``'J'`` (nothing): the client's own pod died, sent right after a final ``'b'`` (I §8.12)."""

    TYPE: ClassVar[str] = "J"

    def body(self) -> bytes:
        return b""

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls()


@message
class LegArrived(ClientMessage):
    """``'K'`` ``id:num``: a guppy or Muncher leg arrived (I §8.13)."""

    TYPE: ClassVar[str] = "K"
    id: int

    def body(self) -> bytes:
        return Writer().num(self.id).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num())


@message
class TowerOrder(ClientMessage):
    """``'M'`` ``grid, Xoff, Y, Zoff, mega:b10, priority:b10``: a tower order at the client's own
    position (I §8.15)."""

    TYPE: ClassVar[str] = "M"
    pos: WirePos
    mega: int
    priority: int

    def body(self) -> bytes:
        w = Writer()
        self.pos.write(w)
        return w.b10(self.mega).b10(self.priority).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(WirePos.read(r), read_b10(r), read_b10(r))


@message
class PickupRequest(ClientMessage):
    """``'Q'`` sub 0 ``object:num``: pickup of a dropped module within 50 ft (I §8.19)."""

    TYPE: ClassVar[str] = "Q"
    SUBS: ClassVar[tuple[int, ...]] = (bias10_encode(0),)
    object: int

    def body(self) -> bytes:
        return Writer().b10(0).num(self.object).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num())


@message
class EjectRequest(ClientMessage):
    """``'Q'`` sub 1 ``module:num``: eject; the bay is already empty (I §8.19)."""

    TYPE: ClassVar[str] = "Q"
    SUBS: ClassVar[tuple[int, ...]] = (bias10_encode(1),)
    module: int

    def body(self) -> bytes:
        return Writer().b10(1).num(self.module).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num())


@message
class ModuleRested(ClientMessage):
    """``'Q'`` sub 3 ``object:num, grid, Xoff, Y, Zoff``: a dropped module came to rest (I
    §8.19)."""

    TYPE: ClassVar[str] = "Q"
    SUBS: ClassVar[tuple[int, ...]] = (bias10_encode(3),)
    object: int
    pos: WirePos

    def body(self) -> bytes:
        w = Writer().b10(3).num(self.object)
        self.pos.write(w)
        return w.bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num(), WirePos.read(r))


@message
class ModuleUsedUp(ClientMessage):
    """``'Q'`` sub 4 ``module:num``: a one-shot module used up or destroyed; no reply (I §8.19)."""

    TYPE: ClassVar[str] = "Q"
    SUBS: ClassVar[tuple[int, ...]] = (bias10_encode(4),)
    module: int

    def body(self) -> bytes:
        return Writer().b10(4).num(self.module).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num())


@message
class Flags(ClientMessage):
    """``'S'`` ``flags:b10``: the client's pod flag byte changed, or the pod was created (I
    §8.21)."""

    TYPE: ClassVar[str] = "S"
    flags: int

    def body(self) -> bytes:
        return Writer().b10(self.flags).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(read_b10(r))


@message
class Chat(ClientMessage):
    """``'U'`` ``style:b10, text:str``: chat the player typed; style 0 open, 1 team. Not shown until
    echoed as ``server.Chat`` (I §8.23). ``text`` is raw: pass it through
    ``server.sanitize_chat``."""

    TYPE: ClassVar[str] = "U"
    style: int
    text: bytes

    def body(self) -> bytes:
        return Writer().b10(self.style).str(self.text).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(read_b10(r), r.str())


@message
class PodState(ClientMessage):
    """``'b'`` ``grid, Xoff, Y, Zoff, orient:num, fwd:b88, fwd_target:b88, v1:num, v2:num,
    state:b10, power:num, weight:b10, armor0:num, armor1:num``: the client's pod state, about
    once or twice a second (I §8.24). ``v1``/``v2`` mean vertical velocity when ``state == 1``,
    turn rate otherwise."""

    TYPE: ClassVar[str] = "b"
    pos: WirePos
    orient: int
    fwd: int
    fwd_target: int
    v1: int
    v2: int
    state: int
    power: int
    weight: int
    armor0: int
    armor1: int

    def body(self) -> bytes:
        w = Writer()
        self.pos.write(w)
        return (
            w.num(self.orient)
            .b88(self.fwd)
            .b88(self.fwd_target)
            .num(self.v1)
            .num(self.v2)
            .b10(self.state)
            .num(self.power)
            .b10(self.weight)
            .num(self.armor0)
            .num(self.armor1)
            .bytes()
        )

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        pos = WirePos.read(r)
        orient = r.num()
        fwd, fwd_target = read_b88(r), read_b88(r)
        v1, v2, state = r.num(), r.num(), read_b10(r)
        power, weight = r.num(), read_b10(r)
        return cls(pos, orient, fwd, fwd_target, v1, v2, state, power, weight, r.num(), r.num())


@message
class Shot(ClientMessage):
    """``'c'`` ``shooter:num, grid, Xoff, Y, Zoff, orient:num, pitch:num, kind:b10``: a shot by the
    client's pod (KIND 9 is a grenade) or an NPC it simulates (I §8.25)."""

    TYPE: ClassVar[str] = "c"
    shooter: int
    pos: WirePos
    orient: int
    pitch: int
    kind: int

    @property
    def reduced_kind(self) -> int:
        """The KIND without the Tri-Shot offset."""
        return reduced_kind(self.kind)

    @property
    def is_safe_kind(self) -> bool:
        """True if relaying this shot cannot crash a client (I §8.25)."""
        return is_safe_kind(self.kind)

    def body(self) -> bytes:
        w = Writer().num(self.shooter)
        self.pos.write(w)
        return w.num(self.orient).num(self.pitch).b10(self.kind).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        shooter = r.num()
        pos = WirePos.read(r)
        return cls(shooter, pos, r.num(), r.num(), read_b10(r))


#: Most tags the client batches into one ``'d'`` (I §4.2, I §8.26: flushed at 11 or end of frame).
ACK_BATCH_MAX = 11


@message
class Acks(ClientMessage):
    """``'d'`` ``tag x n``: raw echoed tag bytes, an acknowledgment of uppercase server packets
    (I §4.2, I §8.26). Accepts any count; ``ACK_BATCH_MAX`` is what the client batches."""

    TYPE: ClassVar[str] = "d"
    tags: bytes

    def body(self) -> bytes:
        return Writer().raw(self.tags).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.rest())
