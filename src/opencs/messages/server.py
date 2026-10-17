"""Server to client messages, one class per row of I §8.

Each class lists its layout and section; ``body()`` encodes the fields after the type letter and
tag, ``read`` decodes them. Constructors refuse every value the client cannot survive (I §4.1, I
§8.20, I §8.22, I §8.25, DESIGN §5), so a bad value raises in our code, never on a player's screen.
Audiences are not here (A.4 belongs to routing, M5).
"""

from __future__ import annotations

from typing import ClassVar, Self

from opencs import policy
from opencs.messages.base import ServerMessage, message, sub_letters
from opencs.messages.catalog import is_module_index
from opencs.messages.fields import (
    WirePos,
    WireXZ,
    check_b88,
    check_callsign,
    check_pod_id,
    check_printable,
    check_range,
    check_slot,
    is_safe_kind,
    make_slot,
    read_b10,
    read_b88,
    reduced_kind,
    split_slot,
)
from opencs.wire.codec import BIAS10_MAX, Reader, Writer, bias10_encode

#: Block grid: 8 x 8 cells, whole-grid ``'T'`` carries all 64 (I §8.22).
BLOCK_COUNT = 64
BLOCK_SIDE = 8


def sanitize_chat(raw: bytes) -> bytes:
    """Make arbitrary client text safe for an ``'a'`` line: every byte outside ``0x20..0x7e``
    becomes a space and the result is cut to ``policy.MAX_CHAT_BYTES`` (I §8.23). The
    session calls this; the ``Chat`` constructor refuses what it would have changed."""
    clean = bytes(b if 0x20 <= b <= 0x7E else policy.CHAT_FILL_BYTE for b in raw)
    return clean[: policy.MAX_CHAT_BYTES]


@message
class ReleaseDrop(ServerMessage):
    """``'A'`` ``id:b10``: release that pod from drop state (I §8.3, I §8.3)."""

    TYPE: ClassVar[str] = "A"
    id: int

    def validate(self) -> None:
        check_pod_id("id", self.id)

    def body(self) -> bytes:
        return Writer().b10(self.id).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(read_b10(r))


@message
class Session(ServerMessage):
    """``'B'`` ``self_id:b10, slot:b10, callsign:str, network:str, city:str, max_mult:b10,
    tower_output:b10, max_module_level:b10, n4:b10, n5:b10``: establish the session (I §8.4,
    I §8.4). ``slot`` is ``(index << 3) | team``; ``n4`` is unread, ``n5`` bounds an unseen list."""

    TYPE: ClassVar[str] = "B"
    self_id: int
    index: int
    team: int
    callsign: bytes
    network: bytes
    city: bytes
    max_mult: int
    tower_output: int
    max_module_level: int
    n4: int = 0
    n5: int = 0

    def validate(self) -> None:
        check_pod_id("self_id", self.self_id)
        check_slot(self.index, self.team)
        check_callsign(self.callsign)
        for name, text, longest in (
            ("network", self.network, policy.MAX_NETWORK_NAME_LENGTH),
            ("city", self.city, policy.MAX_CITY_NAME_LENGTH),
        ):
            check_printable(name, text)
            check_range(f"{name} length", len(text), 1, longest)
        check_range("max_mult", self.max_mult, 1, policy.MAX_MULTIPLIER_CEILING)
        check_range("tower_output", self.tower_output, 0, policy.MAX_TOWER_OUTPUT)
        check_range("max_module_level", self.max_module_level, 0, policy.MAX_MODULE_LEVEL)
        check_range("n4", self.n4, 0, BIAS10_MAX)
        check_range("n5", self.n5, 0, BIAS10_MAX)

    def body(self) -> bytes:
        return (
            Writer()
            .b10(self.self_id)
            .b10(make_slot(self.index, self.team))
            .str(self.callsign)
            .str(self.network)
            .str(self.city)
            .b10(self.max_mult)
            .b10(self.tower_output)
            .b10(self.max_module_level)
            .b10(self.n4)
            .b10(self.n5)
            .bytes()
        )

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        self_id = read_b10(r)
        index, team = split_slot(read_b10(r))
        callsign, network, city = r.str(), r.str(), r.str()
        return cls(
            self_id,
            index,
            team,
            callsign,
            network,
            city,
            read_b10(r),
            read_b10(r),
            read_b10(r),
            read_b10(r),
            read_b10(r),
        )


@message
class QuitAck(ServerMessage):
    """``'C'`` (nothing): quit/kick, back to the Chamber; also the answer to the client's quit
    request (I §8.5)."""

    TYPE: ClassVar[str] = "C"

    def body(self) -> bytes:
        return b""

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls()


@message
class SetStatus(ServerMessage):
    """``'D'`` ``'S'`` ``score:num, multiplier:b10, bay_points:num``: set SCORE, the multiplier
    (and its timer) and bay points (I §8.6)."""

    TYPE: ClassVar[str] = "D"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("S")
    score: int
    multiplier: int
    bay_points: int

    def validate(self) -> None:
        check_range("multiplier", self.multiplier, 0, BIAS10_MAX)

    def body(self) -> bytes:
        return Writer().raw(b"S").num(self.score).b10(self.multiplier).num(self.bay_points).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num(), read_b10(r), r.num())


#: Bays a pod has (I §8.6).
BAY_COUNT = 9


@message
class SetBays(ServerMessage):
    """``'D'`` ``'B'`` ``level x9:b10, marker:b88``: set the nine module-bay levels (0 = no bay,
    1-5 = the highest module level it takes) and the battle marker (I §8.6)."""

    TYPE: ClassVar[str] = "D"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("B")
    levels: tuple[int, ...]
    marker: int

    def validate(self) -> None:
        check_range("bay count", len(self.levels), BAY_COUNT, BAY_COUNT)
        for level in self.levels:
            check_range("bay level", level, 0, policy.MAX_MODULE_LEVEL)
        check_b88("marker", self.marker)

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
class AirSupportReply(ServerMessage):
    """``'E'`` ``'A'`` ``code:num``: Air Support reply, 0 = "Rgr, Air Support" (I §8.7)."""

    TYPE: ClassVar[str] = "E"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("A")
    code: int

    def body(self) -> bytes:
        return Writer().raw(b"A").num(self.code).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num())


@message
class SetBounty(ServerMessage):
    """``'E'`` ``'B'`` ``id:b10, bounty_div10:num``: set a pod's bounty, in tens: seconds of
    accrual (I §8.7)."""

    TYPE: ClassVar[str] = "E"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("B")
    id: int
    bounty_div10: int

    def validate(self) -> None:
        check_pod_id("id", self.id)

    def body(self) -> bytes:
        return Writer().raw(b"B").b10(self.id).num(self.bounty_div10).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(read_b10(r), r.num())


@message
class SendMuncher(ServerMessage):
    """``'E'`` ``'C'`` ``muncher:num, target:num``: send a Muncher at an entity (I §8.7)."""

    TYPE: ClassVar[str] = "E"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("C")
    muncher: int
    target: int

    def body(self) -> bytes:
        return Writer().raw(b"C").num(self.muncher).num(self.target).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num(), r.num())


@message
class GrenadeInFlight(ServerMessage):
    """``'E'`` ``'G'`` ``grid, Xoff, Y, Zoff, yaw:num, pitch:num``: a grenade in flight, the relay
    of a client's ``'E'``-``'G'`` (I §8.7). Angles are tenths of a degree."""

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
class KillNotice(ServerMessage):
    """``'E'`` ``'K'`` ``'E'`` | ``'K'`` ``<other>``: "Earned Kill Recorded" (+1 battle marker) or
    "Kill Recorded" (I §8.13). Any byte but ``'E'`` means not earned; decoding folds them all
    to ``earned=False`` and encoding uses ``policy.KILL_NOT_EARNED_BYTE``."""

    TYPE: ClassVar[str] = "E"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("K")
    earned: bool

    def body(self) -> bytes:
        flag = b"E" if self.earned else bytes([policy.KILL_NOT_EARNED_BYTE])
        return Writer().raw(b"K").raw(flag).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.raw(1) == b"E")


@message
class PingReply(ServerMessage):
    """``'E'`` ``'N'``: the latency ping reply, required, sent at once (I §8.7)."""

    TYPE: ClassVar[str] = "E"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("N")

    def body(self) -> bytes:
        return b"N"

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls()


@message
class ModuleReply(ServerMessage):
    """``'E'`` ``'P'`` ``module:b10, ok:b10``: module-order reply; ``ok`` non-zero = on its way
    (I §8.7)."""

    TYPE: ClassVar[str] = "E"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("P")
    module: int
    ok: int

    def validate(self) -> None:
        if not is_module_index(self.module):
            raise ValueError(f"module {self.module} is not a catalog index (I §9.3.1)")
        check_range("ok", self.ok, 0, BIAS10_MAX)

    def body(self) -> bytes:
        return Writer().raw(b"P").b10(self.module).b10(self.ok).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(read_b10(r), read_b10(r))


@message
class EchoProbe(ServerMessage):
    """``'E'`` ``'c'`` ``value:b10``: an echo probe; the client answers ``'E'`` ``'c'`` ``value:b10
    num`` (I §8.7)."""

    TYPE: ClassVar[str] = "E"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("c")
    value: int

    def validate(self) -> None:
        check_range("value", self.value, 0, BIAS10_MAX)

    def body(self) -> bytes:
        return Writer().raw(b"c").b10(self.value).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(read_b10(r))


@message
class SpawnMine(ServerMessage):
    """``'F'`` ``id:num, variant:b10, grid, Xoff, Y, Zoff``: spawn a mine; ``variant`` is the team
    model 0-3 (I §8.8)."""

    TYPE: ClassVar[str] = "F"
    id: int
    variant: int
    pos: WirePos

    def validate(self) -> None:
        check_range("variant", self.variant, 0, policy.TEAM_COUNT - 1)

    def body(self) -> bytes:
        w = Writer().num(self.id).b10(self.variant)
        self.pos.write(w)
        return w.bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num(), read_b10(r), WirePos.read(r))


@message
class Introduce(ServerMessage):
    """``'G'`` ``id:b10, callsign:str, slot:b10, grid, Xoff, Y, Zoff, orient:num, v1:b88, v2:num,
    bounty_div10:num``: introduce another player's pod, left in drop state: follow with
    ``ReleaseDrop`` (I §6.3). ``v1`` and ``v2`` are sent as 0; the bounty is always present."""

    TYPE: ClassVar[str] = "G"
    id: int
    callsign: bytes
    index: int
    team: int
    pos: WirePos
    orient: int
    v1: int = 0
    v2: int = 0
    bounty_div10: int = 0

    def validate(self) -> None:
        check_pod_id("id", self.id)
        check_slot(self.index, self.team)
        check_callsign(self.callsign)
        check_b88("v1", self.v1)

    def body(self) -> bytes:
        w = Writer().b10(self.id).str(self.callsign).b10(make_slot(self.index, self.team))
        self.pos.write(w)
        return w.num(self.orient).b88(self.v1).num(self.v2).num(self.bounty_div10).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        pod_id = read_b10(r)
        callsign = r.str()
        index, team = split_slot(read_b10(r))
        pos = WirePos.read(r)
        return cls(pod_id, callsign, index, team, pos, r.num(), read_b88(r), r.num(), r.num())


@message
class Delete(ServerMessage):
    """``'H'`` ``id:b10``: delete an entity outright: a player leaving (I §8.10)."""

    TYPE: ClassVar[str] = "H"
    id: int

    def validate(self) -> None:
        check_pod_id("id", self.id)

    def body(self) -> bytes:
        return Writer().b10(self.id).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(read_b10(r))


@message
class Damage(ServerMessage):
    """``'I'`` ``attacker:num, damage:b10``: damage the local pod. ``damage = magnitude * 10 +
    weapon_class``; the attacker is ignored by the client (I §8.11)."""

    TYPE: ClassVar[str] = "I"
    attacker: int
    damage: int

    def validate(self) -> None:
        check_range("damage", self.damage, 0, BIAS10_MAX)
        check_range("weapon class", self.damage % 10, 0, policy.DAMAGE_CLASS_MAX)

    @property
    def weapon_class(self) -> int:
        """The weapon class (damage byte modulo 10)."""
        return self.damage % 10

    @property
    def magnitude(self) -> int:
        """The magnitude (damage byte divided by 10)."""
        return self.damage // 10

    @classmethod
    def of(cls, attacker: int, magnitude: int, weapon_class: int) -> Self:
        """Build from magnitude and class."""
        return cls(attacker, magnitude * 10 + weapon_class)

    def body(self) -> bytes:
        return Writer().num(self.attacker).b10(self.damage).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num(), read_b10(r))


@message
class Destroy(ServerMessage):
    """``'J'`` ``id:num``: destroy an entity; a pod explodes and respawns (I §8.12)."""

    TYPE: ClassVar[str] = "J"
    id: int

    def body(self) -> bytes:
        return Writer().num(self.id).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num())


@message
class MobileCreate(ServerMessage):
    """``'K'`` ``id:num, grid, Xoff, Zoff``: a new id creates a guppy (id < 260) or a Muncher
    there; a known id sends it there (I §8.13, I §8.13)."""

    TYPE: ClassVar[str] = "K"
    id: int
    pos: WireXZ

    def body(self) -> bytes:
        w = Writer().num(self.id)
        self.pos.write(w)
        return w.bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num(), WireXZ.read(r))


@message
class MobileWarp(ServerMessage):
    """``'L'`` ``id:num, grid, Xoff, Zoff``: warp an existing guppy, or set a Muncher's destination;
    an unknown id prints ``ERROR: PF<id>`` (I §8.14)."""

    TYPE: ClassVar[str] = "L"
    id: int
    pos: WireXZ

    def body(self) -> bytes:
        w = Writer().num(self.id)
        self.pos.write(w)
        return w.bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num(), WireXZ.read(r))


@message
class SpawnTower(ServerMessage):
    """``'M'`` ``carrier:num, id:num, team:b10, grid, Xoff, Y, Zoff, mega:b10``: spawn a power
    tower (I §8.15). ``carrier`` 0 = none; ``mega`` is exactly 1 for a MegaTower."""

    TYPE: ClassVar[str] = "M"
    carrier: int
    id: int
    team: int
    pos: WirePos
    mega: int

    def validate(self) -> None:
        check_range("team", self.team, 0, policy.TEAM_COUNT - 1)
        check_range("mega", self.mega, 0, 1)

    def body(self) -> bytes:
        w = Writer().num(self.carrier).num(self.id).b10(self.team)
        self.pos.write(w)
        return w.b10(self.mega).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        carrier, ident, team = r.num(), r.num(), read_b10(r)
        return cls(carrier, ident, team, WirePos.read(r), read_b10(r))


@message
class SpawnSentinel(ServerMessage):
    """``'N'`` ``id:num, grid, Xoff, Zoff``: spawn a Sentinel; it has no Y (I §8.16)."""

    TYPE: ClassVar[str] = "N"
    id: int
    pos: WireXZ

    def body(self) -> bytes:
        w = Writer().num(self.id)
        self.pos.write(w)
        return w.bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num(), WireXZ.read(r))


#: Answers to a tower order: 0 accepted, 1-4 the refusals (I §8.17).
ORDER_REPLY_MAX = 4


@message
class OrderReply(ServerMessage):
    """``'O'`` ``sub:b10`` (0-4): the answer to a tower order: 0 accepted; 1 bad drop point, 2 queue
    full, 3 no relay towers, 4 power saturation (I §8.17)."""

    TYPE: ClassVar[str] = "O"
    result: int

    def validate(self) -> None:
        check_range("result", self.result, 0, ORDER_REPLY_MAX)

    def body(self) -> bytes:
        return Writer().b10(self.result).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(read_b10(r))


@message
class SpawnModule(ServerMessage):
    """``'P'`` ``id:num, module:b10, grid, Xoff, Y, Zoff, vx:num, vy:num, vz:num``: spawn a dropped
    module (I §8.18); ``module`` is a catalog index (I §9.3.1)."""

    TYPE: ClassVar[str] = "P"
    id: int
    module: int
    pos: WirePos
    vx: int = 0
    vy: int = 0
    vz: int = 0

    def validate(self) -> None:
        if not is_module_index(self.module):
            raise ValueError(f"module {self.module} is not a catalog index (I §9.3.1)")

    def body(self) -> bytes:
        w = Writer().num(self.id).b10(self.module)
        self.pos.write(w)
        return w.num(self.vx).num(self.vy).num(self.vz).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        ident, module = r.num(), read_b10(r)
        return cls(ident, module, WirePos.read(r), r.num(), r.num(), r.num())


@message
class GrantPickup(ServerMessage):
    """``'Q'`` sub 0 ``value:num``: grant the pickup of object ``value`` (I §8.19)."""

    TYPE: ClassVar[str] = "Q"
    SUBS: ClassVar[tuple[int, ...]] = (bias10_encode(0),)
    object: int

    def body(self) -> bytes:
        return Writer().b10(0).num(self.object).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num())


@message
class InstallModule(ServerMessage):
    """``'Q'`` sub 5 ``value:num``: install module ``value`` into a bay (I §8.19)."""

    TYPE: ClassVar[str] = "Q"
    SUBS: ClassVar[tuple[int, ...]] = (bias10_encode(5),)
    module: int

    def validate(self) -> None:
        if not is_module_index(self.module):
            raise ValueError(f"module {self.module} is not a catalog index (I §9.3.1)")

    def body(self) -> bytes:
        return Writer().b10(5).num(self.module).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num())


@message
class DestroyObject(ServerMessage):
    """``'Q'`` any other sub ``value:num``: destroy object ``value``; test servers use sub 2
    (I §8)."""

    TYPE: ClassVar[str] = "Q"
    TAKES_ANY_SUB: ClassVar[bool] = True
    object: int
    sub: int = policy.DESTROY_OBJECT_SUB

    def validate(self) -> None:
        check_range("sub", self.sub, 0, BIAS10_MAX)
        if self.sub in (0, 5):
            raise ValueError(f"'Q' sub {self.sub} is a grant or an install, not a destroy")

    def body(self) -> bytes:
        return Writer().b10(self.sub).num(self.object).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(r.num(), sub - 0x10)


@message
class Ambient(ServerMessage):
    """``'R'`` ``sub:b10, arg:b10``: ambient weather (I §8.20). Sub 0 palette now, 1 palette
    crossfade (0-4), 2 rain now, 3 rain ramp (0-3), 4 lightning on/off (0-1). The client does not
    check the argument and crashes on a bad one."""

    TYPE: ClassVar[str] = "R"
    sub: int
    arg: int

    def validate(self) -> None:
        check_range("sub", self.sub, 0, 4)
        limit = (
            policy.AMBIENT_PALETTE_MAX
            if self.sub in (0, 1)
            else policy.AMBIENT_RAIN_MAX
            if self.sub in (2, 3)
            else policy.AMBIENT_LIGHTNING_MAX
        )
        check_range(f"'R' sub {self.sub} argument", self.arg, 0, limit)

    def body(self) -> bytes:
        return Writer().b10(self.sub).b10(self.arg).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(read_b10(r), read_b10(r))


@message
class SetFlags(ServerMessage):
    """``'S'`` ``id:b10, flags:b10``: write a pod's flag byte; 8 mounts an automatic gun (I
    §8.21)."""

    TYPE: ClassVar[str] = "S"
    id: int
    flags: int

    def validate(self) -> None:
        check_pod_id("id", self.id)
        check_range("flags", self.flags, 0, BIAS10_MAX)

    def body(self) -> bytes:
        return Writer().b10(self.id).b10(self.flags).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(read_b10(r), read_b10(r))


@message
class SetBlocks(ServerMessage):
    """``'T'`` ``'I'`` ``class x64:b10``: set every block's class, in world order: byte ``8*X + Z``
    is block (X, Z) (I §8.22). Classes are 0-3 only; the client indexes its table unchecked."""

    TYPE: ClassVar[str] = "T"
    SUBS: ClassVar[tuple[int, ...]] = sub_letters("I")
    classes: tuple[int, ...]

    def validate(self) -> None:
        check_range("block count", len(self.classes), BLOCK_COUNT, BLOCK_COUNT)
        for c in self.classes:
            check_range("block class", c, 0, policy.BLOCK_CLASS_MAX)

    def class_at(self, x: int, z: int) -> int:
        """The class of world block (X, Z)."""
        return self.classes[BLOCK_SIDE * x + z]

    def body(self) -> bytes:
        w = Writer().raw(b"I")
        for c in self.classes:
            w.b10(c)
        return w.bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(tuple(read_b10(r) for _ in range(BLOCK_COUNT)))


@message
class SetBlock(ServerMessage):
    """``'T'`` ``<sub ≠ 'I'> cell:b10 class:b10``: set one block's class (I §8.22). The packed
    cell is ``(Z << 4) | X``, transposed from every position byte. The sub byte is only tested
    against ``'I'`` by the client; decoding keeps whatever it was."""

    TYPE: ClassVar[str] = "T"
    TAKES_ANY_SUB: ClassVar[bool] = True
    x: int
    z: int
    block_class: int
    sub: int = policy.BLOCK_CELL_SUB

    def validate(self) -> None:
        check_range("x", self.x, 0, BLOCK_SIDE - 1)
        check_range("z", self.z, 0, BLOCK_SIDE - 1)
        check_range("block class", self.block_class, 0, policy.BLOCK_CLASS_MAX)
        check_range("sub", self.sub, 0x10, 0xFF)
        if self.sub == ord("I"):
            raise ValueError("a single-cell 'T' sub byte must not be 'I' (I §8.22)")

    def body(self) -> bytes:
        return (
            Writer()
            .raw(bytes([self.sub]))
            .b10((self.z << 4) | self.x)
            .b10(self.block_class)
            .bytes()
        )

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        packed = read_b10(r)
        return cls(packed & 0x0F, packed >> 4, read_b10(r), sub)


@message
class Chat(ServerMessage):
    """``'a'`` ``sender:b10, style:b10, text:str``: HUD/chat text (I §8.23). ``sender`` is
    a pod id or ``policy.SYSTEM_SENDER``; ``style`` 0 open, other = shown with ``:``. Lowercase, so
    no tag. The text is printable ASCII and at most ``policy.MAX_CHAT_BYTES``: the constructor
    refuses anything else; use ``sanitize_chat`` first."""

    TYPE: ClassVar[str] = "a"
    sender: int
    style: int
    text: bytes

    def validate(self) -> None:
        check_range("sender", self.sender, 0, BIAS10_MAX)
        check_range("style", self.style, 0, BIAS10_MAX)
        check_printable("text", self.text)
        check_range("text length", len(self.text), 0, policy.MAX_CHAT_BYTES)

    def body(self) -> bytes:
        return Writer().b10(self.sender).b10(self.style).str(self.text).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls(read_b10(r), read_b10(r), r.str())


@message
class PodState(ServerMessage):
    """``'b'`` ``id:b10, grid, Xoff, Y, Zoff, orient:num, fwd:b88, fwd_target:b88, v1:num, v2:num,
    state:b10, weight:b10``: another pod's state, or, naming the local pod with ``fwd_target = 0``,
    a teleport (I §8.24). ``state`` is 0 or 1 only: 4 makes the client discard the packet and 2
    is never relayed."""

    TYPE: ClassVar[str] = "b"
    id: int
    pos: WirePos
    orient: int
    fwd: int
    fwd_target: int
    v1: int
    v2: int
    state: int
    weight: int

    def validate(self) -> None:
        check_pod_id("id", self.id)
        check_b88("fwd", self.fwd)
        check_b88("fwd_target", self.fwd_target)
        check_range("state", self.state, 0, 1)
        check_range("weight", self.weight, 0, BIAS10_MAX)

    def body(self) -> bytes:
        w = Writer().b10(self.id)
        self.pos.write(w)
        return (
            w.num(self.orient)
            .b88(self.fwd)
            .b88(self.fwd_target)
            .num(self.v1)
            .num(self.v2)
            .b10(self.state)
            .b10(self.weight)
            .bytes()
        )

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        pod_id = read_b10(r)
        pos = WirePos.read(r)
        orient = r.num()
        fwd, fwd_target = read_b88(r), read_b88(r)
        v1, v2 = r.num(), r.num()
        return cls(pod_id, pos, orient, fwd, fwd_target, v1, v2, read_b10(r), read_b10(r))


@message
class Shot(ServerMessage):
    """``'c'`` ``id:num, grid, Xoff, Y, Zoff, orient:num, pitch:num, kind:b10``: a shot by entity
    ``id``, relayed or server-made (I §8.25). The reduced KIND must be 1, 2, 3 or 9 (wire 1, 2,
    3, 9, 11, 12, 13); anything else crashes the client."""

    TYPE: ClassVar[str] = "c"
    id: int
    pos: WirePos
    orient: int
    pitch: int
    kind: int

    def validate(self) -> None:
        if not is_safe_kind(self.kind):
            raise ValueError(f"shot KIND {self.kind} would crash the client (I §8.25)")

    @property
    def reduced_kind(self) -> int:
        """The KIND without the Tri-Shot offset."""
        return reduced_kind(self.kind)

    @property
    def is_safe_kind(self) -> bool:
        """Always true for a constructed ``Shot``; the same check the client side exposes."""
        return is_safe_kind(self.kind)

    def body(self) -> bytes:
        w = Writer().num(self.id)
        self.pos.write(w)
        return w.num(self.orient).num(self.pitch).b10(self.kind).bytes()

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        ident = r.num()
        pos = WirePos.read(r)
        return cls(ident, pos, r.num(), r.num(), read_b10(r))
