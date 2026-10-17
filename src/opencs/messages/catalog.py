"""The module catalog: the client's own tables of module indices (I §9.3.1, ``APPLIED``).

These are facts about the client, not choices, so they live beside the messages that name a module
and are shared with the world (M8). A module is named on the wire by its index.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Indices are grouped in tens by level: ``level = (index - 1) // 10 + 1`` (I §9.3.1).
LEVEL_STRIDE = 10


@dataclass(frozen=True, slots=True)
class Module:
    """One catalog row (I §9.3.1)."""

    index: int
    name: str
    level: int
    #: Added to the pod's weight on install, subtracted on removal.
    weight: int
    #: The client's per-module byte (``49c9:2629``): 1 one-shot, 2 toggle, 0 passive (``DERIVED``).
    mode: int


CATALOG: dict[int, Module] = {
    1: Module(1, "High Speed", 1, 1, 0),
    2: Module(2, "High Jump", 1, 1, 0),
    3: Module(3, "Fast Turn", 1, 1, 0),
    4: Module(4, "Recon", 1, 1, 0),
    5: Module(5, "Rocket Pack", 1, 4, 1),
    6: Module(6, "Repair", 1, 1, 1),
    7: Module(7, "Long Shot", 1, 1, 0),
    8: Module(8, "Infra Red", 1, 1, 2),
    9: Module(9, "Resonator", 1, 1, 0),
    11: Module(11, "Immunize", 2, 2, 1),
    12: Module(12, "Rapid Fire", 2, 1, 0),
    13: Module(13, "Priority", 2, 2, 2),
    14: Module(14, "Reserve Power", 2, 2, 1),
    15: Module(15, "Weather", 2, 1, 1),
    16: Module(16, "Mine", 2, 4, 1),
    17: Module(17, "Call Muncher", 2, 2, 1),
    18: Module(18, "Harden", 2, 1, 1),
    21: Module(21, "Mega Tower", 3, 8, 1),
    22: Module(22, "Front Shield", 3, 3, 2),
    23: Module(23, "Stealth", 3, 3, 0),
    24: Module(24, "Phalanx", 3, 4, 2),
    25: Module(25, "Mega Shot", 3, 3, 2),
    26: Module(26, "Tri-Shot", 3, 9, 2),
    27: Module(27, "Rear Shield", 3, 3, 2),
    28: Module(28, "Power Enhance", 3, 3, 0),
    29: Module(29, "Mine Sweep", 3, 8, 1),
    30: Module(30, "Grenade", 3, 3, 1),
    31: Module(31, "Liberator", 4, 7, 1),
    32: Module(32, "Air Support", 4, 4, 1),
    33: Module(33, "Shield Enhnce", 4, 2, 0),
    34: Module(34, "Descrambler", 4, 5, 0),
    35: Module(35, "Performance", 4, 4, 0),
    36: Module(36, "Call Sentinel", 4, 6, 1),
    37: Module(37, "MkIII Radar", 4, 4, 0),
    41: Module(41, "Leech", 5, 8, 0),
    42: Module(42, "Cloak", 5, 3, 2),
    43: Module(43, "Blaster", 5, 6, 0),
    44: Module(44, "Mega Cell", 5, 4, 0),
    45: Module(45, "Drone", 5, 1, 2),
    46: Module(46, "Deflector", 5, 5, 0),
    47: Module(47, "Turret", 5, 4, 0),
}


def is_module_index(index: int) -> bool:
    """True if ``index`` names a module the client has (I §9.3.1)."""
    return index in CATALOG


def module_level(index: int) -> int:
    """The level the client computes for a catalog index (I §9.3.1)."""
    return (index - 1) // LEVEL_STRIDE + 1
