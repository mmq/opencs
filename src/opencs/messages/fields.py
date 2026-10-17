"""Field groups and checks shared by the message classes (I §8, I §6.1, I §6.2, I §8.25).

Readers here reject bytes no encoder produces, so a decoded value can always be re-encoded; the
checks raise ``ValueError`` (server constructors) and the readers ``DecodeError``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from opencs import policy
from opencs.messages.base import DecodeError
from opencs.wire.codec import (
    BIAS10_MAX,
    BIAS88_FORBIDDEN,
    BIAS88_MAX,
    BIAS88_MIN,
    GRID_X_MAX,
    GRID_Z_MAX,
    Reader,
    Writer,
    join_position,
    split_position,
)

#: A pod id is one bias10 byte (I §6.2).
POD_ID_MAX = BIAS10_MAX

#: The reduced KINDs a shot may have (I §8.25). A wire KIND is the reduced one, or reduced + 10
#: for the Tri-Shot variant.
SAFE_REDUCED_KINDS = frozenset({1, 2, 3, 9})
TRI_SHOT_OFFSET = 10
GRENADE_KIND = 9

#: Every wire KIND the server may send or relay: 1, 2, 3, 9 and 11, 12, 13. 19 (a Tri-Shot grenade)
#: is ``UNKNOWN`` in I §8.25, so refused.
SAFE_WIRE_KINDS = frozenset({1, 2, 3, 9, 11, 12, 13})


def read_b10(r: Reader) -> int:
    """A bias10 scalar; a byte below 0x10 is not one (no encoder emits it)."""
    v = r.b10()
    if v < 0:
        raise DecodeError("bias10 byte below 0x10")
    return v


def read_b88(r: Reader) -> int:
    """A bias88 scalar. Bytes below 0x10 are valid (down to -136, I §4.3), except the three frame
    terminators."""
    v = r.b88()
    if v in BIAS88_FORBIDDEN:
        raise DecodeError("bias88 byte is a frame terminator")
    return v


def check_range(name: str, value: int, low: int, high: int) -> None:
    """Raise ``ValueError`` unless ``low <= value <= high``."""
    if not low <= value <= high:
        raise ValueError(f"{name} {value} outside {low}..{high}")


def check_pod_id(name: str, value: int) -> None:
    """A pod id is a bias10 byte: 0..239 (II §8)."""
    check_range(name, value, 0, POD_ID_MAX)


def check_b88(name: str, value: int) -> None:
    """A bias88 value whose byte stays within 0x10..0xff (I §4.1, I §4.3). Narrower than what
    ``read_b88`` accepts: the server keeps its own bytes clear of the terminators."""
    check_range(name, value, BIAS88_MIN, BIAS88_MAX)


def check_printable(name: str, text: bytes) -> None:
    """Printable ASCII only (``0x20..0x7e``): a control byte would arm command mode or end the
    frame (II §4.3, I §4.1)."""
    if any(not 0x20 <= b <= 0x7E for b in text):
        raise ValueError(f"{name} holds a byte outside 0x20..0x7e")


def check_callsign(callsign: bytes) -> None:
    """A callsign the server sends (``'B'``, ``'G'``): 1 to ``MAX_CALLSIGN_LENGTH`` printable ASCII
    bytes, no lower case, so every client sees the same name (II §3; II §3)."""
    check_printable("callsign", callsign)
    check_range("callsign length", len(callsign), 1, policy.MAX_CALLSIGN_LENGTH)
    if callsign != callsign.upper():
        raise ValueError("callsign must be upper case (II §3)")


def make_slot(index: int, team: int) -> int:
    """The wire slot ``(index << 3) | team`` (I §8.4, I §6.3)."""
    return (index << 3) | team


def split_slot(slot: int) -> tuple[int, int]:
    """``(index, team)`` of a wire slot."""
    return slot >> 3, slot & 7


def check_slot(index: int, team: int) -> None:
    """Index and team each 0..3: four teams of four pods (I §6.3, II §6)."""
    check_range("team", team, 0, policy.TEAM_COUNT - 1)
    check_range("index", index, 0, policy.MAX_PLAYERS_PER_TEAM - 1)


def reduced_kind(kind: int) -> int:
    """A shot KIND without the Tri-Shot offset (I §8.25): ``kind - 10`` if above 9."""
    return kind - TRI_SHOT_OFFSET if kind > GRENADE_KIND else kind


def is_safe_kind(kind: int) -> bool:
    """True if the client survives a shot of this KIND (I §8.25)."""
    return kind in SAFE_WIRE_KINDS


@dataclass(frozen=True, slots=True)
class WirePos:
    """``grid, Xoff, Y, Zoff`` (SPEC A conventions): the cell and the feet within it, and the
    altitude. ``world_x``/``world_z`` are world feet (I §6.1)."""

    cell_x: int
    cell_z: int
    x_off: int
    y: int
    z_off: int

    def __post_init__(self) -> None:
        check_range("cell_x", self.cell_x, 0, GRID_X_MAX)
        check_range("cell_z", self.cell_z, 0, GRID_Z_MAX)

    @property
    def world_x(self) -> int:
        """Feet east, from the west edge of the city."""
        return join_position(self.cell_x, self.x_off, self.cell_z, self.z_off)[0]

    @property
    def world_z(self) -> int:
        """Feet north, from the south edge of the city."""
        return join_position(self.cell_x, self.x_off, self.cell_z, self.z_off)[1]

    @classmethod
    def from_world(cls, x: int, y: int, z: int) -> Self:
        """The wire form of world feet (I §6.1)."""
        cx, xo, cz, zo = split_position(x, z)
        return cls(cx, cz, xo, y, zo)

    @classmethod
    def read(cls, r: Reader) -> Self:
        """Read ``grid, Xoff, Y, Zoff``."""
        cx, cz = r.grid()
        xo = r.num()
        y = r.num()
        return cls(cx, cz, xo, y, r.num())

    def write(self, w: Writer) -> None:
        """Append ``grid, Xoff, Y, Zoff``."""
        w.grid(self.cell_x, self.cell_z).num(self.x_off).num(self.y).num(self.z_off)


@dataclass(frozen=True, slots=True)
class WireXZ:
    """``grid, Xoff, Zoff``: a point with no altitude (I §8.13)."""

    cell_x: int
    cell_z: int
    x_off: int
    z_off: int

    def __post_init__(self) -> None:
        check_range("cell_x", self.cell_x, 0, GRID_X_MAX)
        check_range("cell_z", self.cell_z, 0, GRID_Z_MAX)

    @property
    def world_x(self) -> int:
        """Feet east, from the west edge of the city."""
        return join_position(self.cell_x, self.x_off, self.cell_z, self.z_off)[0]

    @property
    def world_z(self) -> int:
        """Feet north, from the south edge of the city."""
        return join_position(self.cell_x, self.x_off, self.cell_z, self.z_off)[1]

    @classmethod
    def from_world(cls, x: int, z: int) -> Self:
        """The wire form of world feet (I §6.1)."""
        cx, xo, cz, zo = split_position(x, z)
        return cls(cx, cz, xo, zo)

    @classmethod
    def read(cls, r: Reader) -> Self:
        """Read ``grid, Xoff, Zoff``."""
        cx, cz = r.grid()
        xo = r.num()
        return cls(cx, cz, xo, r.num())

    def write(self, w: Writer) -> None:
        """Append ``grid, Xoff, Zoff``."""
        w.grid(self.cell_x, self.cell_z).num(self.x_off).num(self.z_off)
