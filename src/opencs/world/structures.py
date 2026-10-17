"""Towers and their durability (I §9.4, II §10).

The client holds no tower health (its hit method only reports an ``'I'``), so durability is ours:
``policy.TOWER_HP`` (a MegaTower twice that), reduced by the client's own per-shot table
(``limits.DAMAGE_BASE``) for every hit report naming the tower, whoever fired and whatever their
team. At 0 the tower is destroyed. The client never reports where a tower came to rest, so a
tower's place is its ordered point.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto

from opencs import policy
from opencs.messages import ServerMessage, WirePos, limits, server
from opencs.wire.codec import CELL_SIZE


def damage_of(damage_byte: int) -> int | None:
    """Hit points a damage byte takes off a structure: the client's table entry for its weapon
    class (``byte % 10``), the magnitude digit ignored. None for a class past the table (I §8.11):
    ignored, as the client would read outside it."""
    weapon = damage_byte % 10
    if weapon > limits.DAMAGE_CLASS_MAX:
        return None
    return limits.DAMAGE_BASE[weapon]


class TowerHit(Enum):
    """What one hit report did to a tower."""

    IGNORED = auto()  # a weapon class past the client's table (I §8.11)
    STANDS = auto()
    DESTROYED = auto()


@dataclass(slots=True)
class Tower:
    """A power tower: who owns it, where it was ordered, how much it can still take."""

    entity_id: int
    team: int
    pos: WirePos
    mega: bool
    hp: int
    max_hp: int
    #: When Muncher immunity ends (M8's Immunize); a tower starts without it. I §9.5.2, II §16.2.
    immune_until: float = 0.0

    @classmethod
    def new(cls, entity_id: int, team: int, pos: WirePos, mega: bool) -> Tower:
        hp = policy.MEGATOWER_HP if mega else policy.TOWER_HP
        return cls(entity_id, team, pos, mega, hp, hp)

    @property
    def block(self) -> tuple[int, int]:
        """The 600 ft block it stands in (it may be off the map): its ordered point's."""
        return self.pos.world_x // CELL_SIZE, self.pos.world_z // CELL_SIZE

    def creating_messages(self, now: float) -> Sequence[ServerMessage]:
        """The ``'M'`` that spawns it, no carrier, falling from the ordered point (I §9.4).
        A late joiner gets the same message as first sent (I §7.3)."""
        return (server.SpawnTower(0, self.entity_id, self.team, self.pos, 1 if self.mega else 0),)

    def take_damage(self, damage_byte: int) -> TowerHit:
        """Apply one hit report's damage byte."""
        amount = damage_of(damage_byte)
        if amount is None:
            return TowerHit.IGNORED
        self.hp -= amount
        return TowerHit.DESTROYED if self.hp <= 0 else TowerHit.STANDS
