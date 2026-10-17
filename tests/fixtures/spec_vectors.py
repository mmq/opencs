"""Frames from I App. A.1 and I App. A.2, byte for byte (real captures). Shared with M2.

``FULL_FRAMES`` are whole frames (with terminator where the capture shows one); ``PAYLOADS`` are
payload-only lines the spec abbreviates. Tag bytes in the A.3b ``'I'`` line are placeholders there,
so it appears only as a payload.
"""

from __future__ import annotations


def h(s: str) -> bytes:
    """Hex with spaces to bytes."""
    return bytes.fromhex(s.replace(" ", ""))


# name -> (direction, whole frame bytes)
FULL_FRAMES: dict[str, tuple[str, bytes]] = {
    "A3_B": (
        "S2C",
        h(
            "43 53 4e b1 3b 42 11 10 10 18 4a4f494e47454e49 15 47454e4945"
            " 1d 435942455243495459204f4e45 18 11 12 10 10 0d"
        ),
    ),
    "A3_S": ("C2S", h("43 53 8a 75 17 53 10")),
    "A3_A": ("S2C", h("43 53 19 e6 18 41 20 10 0d")),
    "A3_b": (
        "C2S",
        h("43 53 25 da 28 62 10 b2 19 12 20 60 12 2e 88 88 10 10 14 12 d8 10 10 10"),
    ),
    "A3_EN": ("C2S", h("43 53 3a c5 17 45 4e")),
    "A3_EN_reply": ("S2C", h("43 53 62 9d 18 45 27 4e 0d")),
    "A3_d": ("C2S", h("43 53 6e 91 1b 64 11 12 13 14 15")),
    "A3_I": ("C2S", h("43 53 69 96 1a 49 24 31 11 10")),
    "A3b_G": (
        "S2C",
        h("43 53 34 cb 2e 47 3a 11 18 4a4f494e47454e32 18 10 12 15 12 20 12 15 12 2e 88 10 10 0d"),
    ),
    "A3b_A": ("S2C", h("43 53 35 ca 18 41 3b 11 0d")),
    "A3b_b": (
        "S2C",
        h("43 53 49 b6 26 62 11 10 12 15 12 20 12 15 12 2e 88 88 10 10 10 10 0d"),
    ),
    "A3b_E_grenade_c2s": ("C2S", h("43 53 2f d0 22 45 47 32 b2 16 d2 14 12 24 f2 49 32 ee")),
    "A3b_E_grenade_s2c": (
        "S2C",
        h("43 53 37 c8 23 45 87 47 32 b2 16 d2 14 12 24 f2 49 32 ee 0d"),
    ),
    "A3b_J_s2c": ("S2C", h("43 53 5f a0 18 4a 4d 20 0d")),
    "A3b_J_c2s": ("C2S", h("43 53 70 8f 16 4a")),
    "A3b_J0_s2c": ("S2C", h("43 53 51 ae 18 4a 4f 10 0d")),
    "A3b_A_c2s": ("C2S", h("43 53 67 98 16 41")),
    "A3b_U": ("C2S", h("43 53 7b 84 1a 55 10 12 6f 6b")),
    "A3b_a": ("S2C", h("43 53 18 e7 1b 61 10 10 12 6f 6b 0d")),
    "A3b_C_c2s": ("C2S", h("43 53 69 96 16 43")),
    "A3b_C_s2c": ("S2C", h("43 53 4f b0 17 43 65 0d")),
}

# Payload-only lines (the spec shows "…" for the header).
PAYLOADS: dict[str, bytes] = {
    "A3_M": h("4d 10 f2 1e 32 14 32 26 10 10"),
    "A3_K": h("4b b2 20"),
    "A3_Q": h("51 10 32 12"),
    "A3b_c": h("63 10 21 d2 1d 32 1e b2 1f 22 e5 10 11"),
    "A3b_I_c2s": h("49 20 10"),
}
