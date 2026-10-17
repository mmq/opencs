"""The server's built-in settings, each citing where it comes from (DESIGN §4).

The limits, timeouts and game numbers the spec leaves to the server (I §10) live here, never
inline in the code. Some are the defaults for settings in ``config/server.toml`` and the city
files (``opencs.config``); the rest are fixed.
"""

# --- Server ----------------------------------------------------------------------------------

#: TCP port the listener binds by default (I §3: transport is TCP). Ours.
DEFAULT_PORT = 2399

#: Address the listener binds by default: every interface, so a LAN or bridged client can reach it.
#: Ours; set ``host`` in server.toml to narrow it.
DEFAULT_HOST = "0.0.0.0"

#: State directory, relative to the config file (the repo's ``data/`` beside ``config/``). Ours.
DEFAULT_DATA_DIR = "../data"

#: Directory of per-city files, relative to the config file. Ours.
DEFAULT_CITIES_DIR = "cities"

#: Log level when none is configured. Ours.
DEFAULT_LOG_LEVEL = "INFO"

#: Valid TCP port numbers (0 asks the OS for a free one). I §3: transport is TCP.
MIN_PORT = 0
MAX_PORT = 65535

#: City ticks per second. I §7.1, II §7: 5 per second.
TICK_HZ = 5

# --- Cities (II §5, I §9.2, II §5) ----------------------

#: Longest city name, in characters. Ours (the test server's limit); the spec is silent.
MAX_CITY_NAME_LENGTH = 16

#: Printable ASCII range allowed in text the server sends to clients (space to tilde). A 0x01 or
#: 0x05 byte arms command mode on the receiving client, so no control byte may be relayed.
#: II §4.3, I §7.2.
PRINTABLE_LOW = " "
PRINTABLE_HIGH = "~"

#: Highest max multiplier a city may set; larger overflows the score arithmetic. I §9.2.6.
MAX_MULTIPLIER_CEILING = 51

#: Tower output levels: 0 Low, 1 Normal, 2 High. II §5 (n2).
MAX_TOWER_OUTPUT = 2

#: Highest module level. II §5 (n3).
MAX_MODULE_LEVEL = 5

#: Lowest and highest module catalog index; the real indices are 1-9, 11-18, 21-30, 31-37 and
#: 41-47. I §9.3.1.
MODULE_INDEX_LOW = 1
MODULE_INDEX_HIGH = 47

#: Players per city. II §6 (S9: "Capacity 16").
DEFAULT_CITY_CAPACITY = 16

#: Teams a city has (0-3: RED, BLUE, GREEN, BLACK). II §6.
TEAM_COUNT = 4

#: Most pods one team may field; the client's limit. II §6.
MAX_PLAYERS_PER_TEAM = 4

#: Most players a city can hold: every team full. II §6, I §6.3
#: (each player needs a unique team/index pair in the roster).
MAX_CITY_CAPACITY = TEAM_COUNT * MAX_PLAYERS_PER_TEAM

#: How a requested team is honored when it has no room. II §6.
TEAM_POLICIES = ("request-or-balance", "balance", "random")
DEFAULT_TEAM_POLICY = "request-or-balance"

#: The city a bare ``/PLAY`` picks when every city is empty. II §5.
DEFAULT_CITY_LETTER = "B"

#: Map numbers a city may name: the launch sends ``E <map> <map>`` and ``<rev>`` must be a single
#: digit (I §5.4). 0 is excluded: the client's files are numbered from 1.
MAP_NUMBER_LOW = 1
MAP_NUMBER_HIGH = 9

# --- Identity (II §3, II §3; DESIGN §5) ------------------------------------

#: The login name of a stock install, compared case-insensitively: no password, assigned callsign.
#: I §5.1, II §3.
GUEST_ACCOUNT_NAME = "JOINGENIE"

#: Longest and shortest account name, and longest password, accepted on a login line. Ours: the
#: spec fixes the line's shape (``NAME,PASSWORD``) but no lengths. The line itself is bounded by
#: ``MAX_LINE_BYTES``.
MAX_ACCOUNT_NAME_LENGTH = 32
MAX_PASSWORD_LENGTH = 64

#: PBKDF2-HMAC-SHA256 work factor and salt size. Ours (DESIGN §5 "salted PBKDF2"). 100 000 rounds
#: is about 30 ms, which blocks the event loop once per login; raise it when logins move to a
#: thread.
PBKDF2_ITERATIONS = 100_000
PBKDF2_SALT_BYTES = 16

#: The first and last single-digit suffix of a duplicate callsign (``JOINGEN2`` .. ``JOINGEN9``;
#: II §3: the 8th character is replaced by a digit), then two-digit suffixes
#: (``JOINGE10`` .. ``JOINGE99``) when those run out. The 9th duplicate and beyond are ours, see
#: II §3.
CALLSIGN_FIRST_SUFFIX = 2
CALLSIGN_LAST_SUFFIX = 99

# --- Wire (I §5.2, I §4.1; DESIGN §5) ------------------------------------------------------------

#: Longest inbound Chamber line kept, in bytes; longer lines are discarded and counted. The
#: client's own armed buffer is 512 bytes (I §5.2), and no lobby command comes near it. Ours
#: (DESIGN §5 "line length in the Chamber").
MAX_LINE_BYTES = 512

# --- Messages (I §8, I §8, I §8.20) ----------------------------------------------------

#: Longest callsign the server sends in ``'B'``/``'G'``. II §3: 8 is
#: our choice; test servers have always sent it.
MAX_CALLSIGN_LENGTH = 8

#: Longest network name in ``'B'`` (the banner reads "City: <city> on <network>"). Ours; the spec
#: gives none; the real one is 5 characters.
MAX_NETWORK_NAME_LENGTH = 16

#: Replaces each byte outside ``0x20..0x7e`` in chat text (I §8.23: "replace 0x0a/0x0d/0x0e").
CHAT_FILL_BYTE = 0x20

#: The sub byte of a single-cell ``'T'``: the client only tests it against ``'I'`` (I §8.22,
#: A.1); test servers use ``'C'``. Ours.
BLOCK_CELL_SUB = ord("C")

#: The byte after ``'E'``-``'K'`` for "Kill Recorded" (not earned); any byte but ``'E'`` works
#: (I §8.7). Ours.
KILL_NOT_EARNED_BYTE = ord("N")

#: The ``'Q'`` sub the server sends to destroy an object (any sub but 0 and 5 does; I §8).
#: Ours.
DESTROY_OBJECT_SUB = 2

#: Highest ``'R'`` lightning argument ("on/off"). Ours: the spec bounds only palette and rain (those
#: client limits are in ``messages/limits.py``); I §8.20.
AMBIENT_LIGHTNING_MAX = 1
