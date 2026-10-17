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

# --- Connections (DESIGN §3.3, §5) --------------------------------------------------------------

#: Connections accepted in total and from one address; more are closed at once. Ours (DESIGN §5
#: "connections in total and per address"): a city holds 16 and there are 3, so 64 is every seat
#: plus those in the Chamber; 8 per address lets several clients share a host or a NAT.
MAX_CONNECTIONS = 64
MAX_CONNECTIONS_PER_ADDRESS = 8

#: Items (raw writes or messages) a connection may have queued for sending before it is closed.
#: Ours (DESIGN §3.3): the client handles about 50 packets a second (I §3), so this is over
#: twenty seconds of backlog for a client that has stopped reading.
OUTBOX_LIMIT = 1024

#: Session ticks per second per connection (DESIGN §3.4): fine enough for the 0.3 s login prompt
#: delay. Ours.
SESSION_TICK_HZ = 10

#: Bytes read from a socket at once. Ours.
READ_CHUNK = 4096

#: Seconds a closing connection may take to flush its queue before it is cut. Ours.
CLOSE_FLUSH_TIMEOUT = 5.0

#: TCP keepalive on accepted sockets, so a peer that vanished without a FIN is noticed and its
#: account freed (DESIGN §5): idle seconds before the first probe, seconds between probes, probes
#: before the connection is dropped. Ours. (A Chamber idle timeout is M11's.)
KEEPALIVE_IDLE = 60
KEEPALIVE_INTERVAL = 15
KEEPALIVE_PROBES = 4

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

#: The team names, indexed by team number 0-3. II §6.
TEAM_NAMES = ("RED", "BLUE", "GREEN", "BLACK")

#: The city a bare ``/PLAY`` picks when every city is empty. II §5.
DEFAULT_CITY_LETTER = "B"

#: Map numbers a city may name: the launch sends ``E <map> <map>`` and ``<rev>`` must be a single
#: digit (I §5.4). 0 is excluded: the client's files are numbered from 1.
MAP_NUMBER_LOW = 1
MAP_NUMBER_HIGH = 9

# --- Login and Chamber timing (I §5.1, II §4.1; DESIGN §5) -------------------------------------

#: Delay after the account line before the ``\r\n*`` prompt is sent if ``<Not Telling!>`` has not
#: arrived first, in seconds. I §5.1, II §2: 0.3 s.
LOGIN_PROMPT_DELAY = 0.3

#: Seconds from connecting to a completed account line; later, seconds of silence tolerated at
#: the GEnie ``*`` prompt (a player may sit at its menus). Ours (DESIGN §5 "time allowed to log
#: in"): the spec gives neither.
LOGIN_TIMEOUT = 60.0
GENIE_IDLE_TIMEOUT = 900.0

#: Most lines accepted from a connection between connecting and entering the Chamber (empty lines
#: and ``<Not Telling!>`` included), and most bytes accepted before ``HHH``. Ours (DESIGN §5
#: "login attempts"): a real client sends 4 lines and about 20 bytes of modem noise.
LOGIN_MAX_LINES = 50
LOGIN_PREAMBLE_LIMIT = 4096

#: How long to wait for the reply to ``PRESS RETURN`` before entering anyway, and for ``/GO`` after
#: ``E <map> <map>``, in seconds. II §4.1 (5 s), I §5.4, II §4.5 (15 s, a server choice).
VERSION_WAIT = 5.0
GO_WAIT = 15.0

#: Bad inbound frames and undecodable messages tolerated per window before a connection is closed
#: (DESIGN §5 ``MAX_BAD_FRAMES``). A window rather than a lifetime total, because real clients send
#: some: 24 consecutive undecodable ``'b'`` frames, one a second, were captured (I §4.1), i.e. at
#: most 10 per 10 s. Ours.
MAX_BAD_INPUT = 50
BAD_INPUT_WINDOW = 10.0

#: Seconds a partial frame may wait before the reader gives up on its header and re-scans (a stray
#: valid-looking header can hold back real frames, I §4.1). A real frame is at
#: most 250 bytes, so it completes within a fraction of a second even at modem speed. Ours.
FRAME_STALL_SECONDS = 3.0

# --- Lobby text (II §4.4, "Relay hygiene") ------------------------------------------

#: Client text colors (``0x05 D<n>``): server text D2, headings D6, chat D4, errors D3, each block
#: ending with D0, the client's own color. Owner's choice 2026-10-08 (II §4.4).
COLOR_TEXT = 2
COLOR_HEADING = 6
COLOR_CHAT = 4
COLOR_ERROR = 3
COLOR_RESET = 0

#: The seven usable ``D`` digits (7-9 are black on black; I §5.2), shown by ``/COLORS``.
USABLE_COLORS = tuple(range(7))

#: Longest relayed chat text, in characters. Ours (II §4.3: "keep lines short"; the test server's
#: value).
CHAT_MAX_CHARS = 72

#: Per-connection limit on lines in the Chamber (DESIGN §5 "Input is hostile"): a token bucket of
#: ``CHAMBER_BURST_LINES`` refilled at ``CHAMBER_LINES_PER_SECOND``. Each chat line is relayed to
#: every other member, so an unlimited sender could overflow their outboxes and disconnect them.
#: A player types far slower; a button press sends a few lines at once. Excess lines are dropped
#: with one warning; more than ``CHAMBER_FLOOD_DROPS`` drops in ``CHAMBER_FLOOD_WINDOW`` seconds
#: close the connection. Ours (the spec sets no rate).
CHAMBER_BURST_LINES = 8
CHAMBER_LINES_PER_SECOND = 2.0
CHAMBER_FLOOD_DROPS = 40
CHAMBER_FLOOD_WINDOW = 10.0

#: How long after a bare ``/CITY`` a bare city letter counts as the button's second half and gets
#: the hint line (II §4.2: "within 2 min"), in seconds.
CITY_HINT_WINDOW = 120.0

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

# --- Entity ids (I §6.2) ----------------------------------------------------------------------

#: Id ranges, inclusive ``(low, high)``. II §8, all ours. Pod messages
#: carry the id in one bias10 byte, so only pods need the 0-239 space; everything else stays above.
PLAYER_IDS = (0, 31)
RIVAL_POD_IDS = (32, 239)
GUPPY_IDS = (250, 259)
MUNCHER_IDS = (260, 4095)
SENTINEL_IDS = (4096, 8191)
STRUCTURE_IDS = (8192, 32767)  # towers, module drops, mines, called Sentinels

# --- In-game session and pods (I §7.1, I §5.5) -------------------------------------------------

#: The network name in ``'B'``; the banner reads "City: <city> on <network>". I App. A.1: the
#: worked session sends "GENIE".
NETWORK_NAME = "GENIE"

#: The ``'B'`` is repeated every 0.2 s until the client's first frame, capped at 3500 bytes so the
#: client's 4096-byte ring cannot overrun while its engine loads. I §7.1, II §7 (our timing).
B_REPEAT_INTERVAL = 0.2
B_REPEAT_CAP_BYTES = 3500

#: After a death, seconds to wait for the client's bare ``'A'`` before answering state-4 reports
#: again. I §7.1, II §7: 20 s (a server choice).
DROP_REQUEST_TIMEOUT = 20.0

#: Seconds to wait for the first, then the second, raw ``QUIT\r`` after answering the client's quit,
#: and from the last one seen until the Chamber's buttons are restored with ``0x05 "A\r"``.
#: I §5.5 step 5 (all server choices: 10 s, 1 s, 3 s).
QUIT_WAIT = 10.0
QUIT_SECOND_WAIT = 1.0
RETURN_SETTLE = 3.0
#: After the return, a missing ``QUIT\r`` (of the two the client sends) that still arrives within
#: this long is swallowed, not relayed as chat (I §5.5 step 4, II §4.3 relay hygiene; our choice).
LATE_QUIT_WINDOW = 10.0

#: The pod state byte of a pod in drop state (I §7.1, I §7.4): it discards positions.
POD_STATE_DROPPING = 4

#: The pod state byte of a falling pod (I §8.24): its ``v1``/``v2`` are vertical velocity, and it
#: is the one state besides 0 that a relayed ``'b'`` may carry.
POD_STATE_FALLING = 1
