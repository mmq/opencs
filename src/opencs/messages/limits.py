"""Limits that are facts about the client or the frame, not choices (I §4.1, I §8, I §8.20).

The client keeps tables of these sizes and does not check what it is given, so a larger value
reads past a table and crashes it (I §4.1, I §8.20, I §8.22, I §8.25). They must not be tuned. The
server's own choices are in ``policy.py``.
"""

from __future__ import annotations

#: Most bytes of chat/HUD text in one ``'a'`` frame: the 234-byte payload less type, sender, style
#: and length bytes. I §8.23, I §4.1.
MAX_CHAT_BYTES = 230

#: The ``'a'`` sender id that prints a system line (``- text``) and needs no pod. I §8.23.
SYSTEM_SENDER = 100

#: Highest ``'R'`` palette (subs 0, 1) and rain (subs 2, 3) argument: the client indexes its tables
#: with them unchecked. I §8.20, I §4.1, I §8.20, I §8.22, I §8.25.
AMBIENT_PALETTE_MAX = 4
AMBIENT_RAIN_MAX = 3

#: Block classes the client's value table holds: 0-3. I §8.20.
BLOCK_CLASS_MAX = 3

#: Damage weapon classes the client's base-damage table holds: 0-4. I §8.11.
DAMAGE_CLASS_MAX = 4

#: Module bays a pod has. I §8/I §8.6.
BAY_COUNT = 9
