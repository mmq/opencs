"""The server's built-in settings, each citing where it comes from (DESIGN §4).

The limits, timeouts and game numbers the spec leaves to the server (I §10) live here, never
inline in the code. Some are the defaults for settings in ``config/server.toml`` and the city
files (``opencs.config``); the rest are fixed.
"""

# --- Server ----------------------------------------------------------------------------------

#: TCP port the listener binds by default (I §3: transport is TCP). Ours.
DEFAULT_PORT = 2399

#: City ticks per second. I §7.1, II §7: 5 per second.
TICK_HZ = 5

# --- Cities (II §5, I §9.2, II §5) ----------------------

#: Longest city name, in characters. II §5.
MAX_CITY_NAME_LENGTH = 16

#: Highest max multiplier a city may set; larger overflows the score arithmetic. I §9.2.6.
MAX_MULTIPLIER_CEILING = 51

#: Tower output levels: 0 Low, 1 Normal, 2 High. II §5 (n2).
MAX_TOWER_OUTPUT = 2

#: Highest module level. II §5 (n3).
MAX_MODULE_LEVEL = 5

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
