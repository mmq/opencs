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

#: The chat styles the client sends: 0 the open channel, 1 the team channel. I §8.23.
CHAT_STYLE_OPEN = 0
CHAT_STYLE_TEAM = 1

#: Highest ``'R'`` palette (subs 0, 1) and rain (subs 2, 3) argument: the client indexes its tables
#: with them unchecked. I §8.20, I §4.1, I §8.20, I §8.22, I §8.25.
AMBIENT_PALETTE_MAX = 4
AMBIENT_RAIN_MAX = 3

#: Block classes the client's value table holds: 0-3. I §8.20.
BLOCK_CLASS_MAX = 3

#: The points a block of each class is worth per second of control, before the multiplier: the
#: client's value table. I §8.22, I §9.2.6.
BLOCK_VALUES = (1, 2, 5, 10)

#: The client adds territory income as a 16-bit product, so ``sum(controlled values) x multiplier``
#: must stay below this or SCORE goes down instead of up. I §9.2.6.
SCORE_PRODUCT_LIMIT = 32768

#: The city is 8 x 8 blocks of 600 ft. I §6.1, I §8.20.
BLOCK_SIDE = 8

#: Damage weapon classes the client's base-damage table holds: 0-4. I §8.11.
DAMAGE_CLASS_MAX = 4

#: Module bays a pod has. I §8/I §8.6.
BAY_COUNT = 9

#: What each weapon class of a damage byte (``byte % 10``) subtracts from a tower or other thing the
#: server keeps health for: the client's own per-shot table, shared by pods. I §8.11, II §10.
DAMAGE_BASE = (150, 150, 300, 450, 500)
