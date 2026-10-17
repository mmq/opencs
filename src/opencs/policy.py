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

#: The SQLite file inside ``data_dir``. Ours (DESIGN §2 "Persistence"; one file, no server).
DATABASE_FILE_NAME = "opencs.db"

#: Prefix of a real account's progress key: ``acct:NAME``. A guest's key is its bare callsign,
#: which is upper case, so the two can never collide. II §3.
ACCOUNT_KEY_PREFIX = "acct:"

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

#: Per-session limits on the client messages the city relays to other players (DESIGN §5 "Input is
#: hostile"; I §8, I §8.24, I §8.25, I §9.3.5): a token bucket per kind, ``(burst, per second)``.
#: One relayed frame lands in every other outbox, so an unlimited sender could overflow them and get
#: the receivers disconnected (``OUTBOX_LIMIT``). Excess frames are dropped and counted as bad
#: input, so a sustained flood closes the flooder's own connection (``MAX_BAD_INPUT``). Sized from
#: the real captures, whose peaks per kind are: 'b' 3 in 1 s and 20 in 10 s; shots (Rapid Fire,
#: Tri-Shot) 3 in 1 s and 17 in 10 s; chat 1 per second; Grenades 1 per second. Each limit is about
#: four times the sustained peak with a burst of several seconds' worth. Ours.
RELAY_LIMIT_POD_STATE = (12, 8.0)
RELAY_LIMIT_SHOT = (40, 16.0)
RELAY_LIMIT_CHAT = (8, 2.0)
RELAY_LIMIT_GRENADE = (8, 4.0)

# --- Combat and scoring (I §9.1, I §9.2, I §9.2, II §9; DESIGN §3.1 ``combat``/``scoring``) ------

#: Seconds from a client's ``'c'`` to the ``'I'`` its simulation reports for that shot. II §9.3:
#: "~1.0 s earlier" (observed: own-shot delays span 0.75 to 1.14 s). Derived, not a choice.
SHOT_FLIGHT_TIME = 1.0

#: How far from ``SHOT_FLIGHT_TIME`` a logged shot may be and still be a candidate for a hit
#: report, in seconds (candidates lie 0.0 to 2.0 s back). II §9.3: the +-1.0 s window is
#: a server choice.
SHOT_MATCH_TOLERANCE = 1.0

#: Most shots one connection's log keeps. Ours (DESIGN §5): shots are also aged out past the match
#: window, so this only bounds a client that fires faster than any real one (the relay limit
#: ``RELAY_LIMIT_SHOT`` allows 16 a second, so this is about two windows' worth).
SHOT_LOG_MAX = 64

#: A hit counts toward a bounty payout for this many seconds. I §9.2.8, II §11.2 (the manual: "every
#: non-teammate who hit that pod in the preceding 20 seconds"), II §11.2.
PAYOUT_WINDOW = 20.0

#: Bounty wire units a living pod accrues per second: ``'E'``-``'B'`` carries seconds of accrual
#: (the client shows it times 10). I §9.2, I §9.2.2.
BOUNTY_UNITS_PER_SECOND = 1

#: The client displays and credits ten points per wire unit. I §8.7 ("The client stores
#: ``bounty x 10``").
BOUNTY_DISPLAY_FACTOR = 10

#: Seconds between a pod's bounty pushes to the clients. I §9.2.12, II §11.2 gives the channel but
#: not the cadence (a guess). The client counts +10 a tick of its own between pushes once
#: ``'D'``-``'B'`` is sent, so a slow cadence only corrects drift. Ours.
BOUNTY_PUSH_INTERVAL = 10.0

#: The first ``'D'``-``'S'`` the client sends at least this long after a death gets the multiplier
#: floor. II §12 ("the first ``'D'``-``'S'`` the client sends >= 2 s after a death"): an
#: earlier report may have been built before our ``'J'`` reset the client (a server choice).
FLOOR_SETTLE = 2.0

#: A new player's bay levels and battle marker: ``1 1 1 1 0 0 0 0 0``, marker 0 (level 0 = no bay,
#: ``X``). I §8.6, II §11.3 (persistence, which replaces this, is M10's).
NEW_PLAYER_BAYS = (1, 1, 1, 1, 0, 0, 0, 0, 0)
NEW_PLAYER_MARKER = 0

#: After a restore, a client ``'D'``-``'S'`` is not saved until one shows at least the restored
#: SCORE and bay points, or this many seconds have passed. A report built before the restore
#: landed arrives within a frame or two, so 10 s is generous. II §11.3
#: (a guess).
RESTORE_SETTLE = 10.0

#: Bay points a bay's upgrade spends, indexed by the bay's level BEFORE the upgrade (0 = an empty
#: bay). I §9.2.7 and II §11.3: 1 000 / 10^4 / 10^5 / 10^6 / 10^7 / 10^8.
BAY_UPGRADE_POINTS = (1_000, 10**4, 10**5, 10**6, 10**7, 10**8)

#: A spawn's grace period ends after this many seconds even if the pod never leaves its home box.
#: I §9.1.5 ("the two-minute deadline").
GRACE_TIMEOUT = 120.0

#: Each team's home corner tile ``(cell x, cell z)``, indexed by team 0-3 (red, blue, green,
#: black): the client's spawn tables. I §9.4.3, I §9.1.5.
TEAM_CORNER_CELLS = ((0, 0), (7, 0), (0, 7), (7, 7))  # tile 0 or 7 (``codec.CITY_CELLS`` - 1)

#: The grace box is the 3x3 tiles centered on the corner tile: it starts one tile before the
#: corner tile and is three tiles long. I §9.1.5 (``corner x 600 - 600`` to that ``+ 1800``).
GRACE_BOX_START_TILES = -1
GRACE_BOX_TILES = 3

#: Per-session limit on the ``'I'`` hit reports a client may send, a token bucket ``(burst, per
#: second)``. Each one that credits a pod's damage lands in the victim's outbox (I §9.1.4), so
#: an unlimited reporter could overflow it. Real peaks are a few a second (a hit per shot, shots
#: at most 3 a second); four times that with a burst of several seconds' worth, as the other
#: relayed kinds (``RELAY_LIMIT_*``). Ours.
RELAY_LIMIT_HIT = (40, 16.0)

# --- Towers, guppies and territory (I §9.4, II §10, II §5, II §14; DESIGN §3.1 ``structures``) ---

#: Damage a tower absorbs before it is destroyed, and a MegaTower's (twice: the manual's 2:1 ratio).
#: The client holds no tower health, so the number is ours: a full pod's power, so the same per-shot
#: table applies and the starting gun's 150 destroys a tower in 20 hits. II §10.
TOWER_HP = 3000
MEGATOWER_HP = 6000

#: A tower order meeting this many waiting jobs is refused with ``'O'`` sub 2 ("mission queue
#: full", refunded). The client has the line, the number is ours. II §14, II §14.
MAX_WAITING_TOWER_ORDERS = 4

#: The ``'O'`` answers to a tower order: accepted, bad drop point, mission queue full. I §8.17, I
#: §9.4.2.
ORDER_ACCEPTED = 0
ORDER_BAD_DROP_POINT = 1
ORDER_QUEUE_FULL = 2

#: The first guppy id; team ``t``'s guppy is ``GUPPY_BASE_ID + t``, which also picks its color
#: and its cruise altitude. I §6.2, I §9.4.1, II §14.
GUPPY_BASE_ID = GUPPY_IDS[0]

#: Per-session limit on orders (tower and module), a token bucket ``(burst, per second)``, as
#: ``RELAY_LIMIT_*`` (DESIGN §5). A player orders at most once a second (the manual's one purchase
#: per second, II §14); each accepted order queues work, so an unlimited sender is bounded here
#: as well as by the queue. Ours.
RELAY_LIMIT_ORDER = (4, 1.0)

# --- Modules and effects (I §9.4, I §9.3.1, I §9.3.5, II §16.1, II §13, II §15, II §5; DESIGN §3.1)
# -----------

#: The palette a city starts in and resets to when it empties: our choice, 0 "bright daylight" as
#: on test servers. It need not equal the client's own default
#: (``limits.CLIENT_DEFAULT_PALETTE``), which decides what a joiner already shows. I §8.20, I §8.7,
#: I §9.3.5.
DEFAULT_PALETTE = 0

#: The ``'E'``-``'P'`` ok byte: 1 = "Rgr, module drop", 0 = "no X modules in stock" and nothing is
#: delivered (a refusal never refunds anything, so never ``'O'``). I §8.7.
MODULE_ORDER_ACCEPTED = 1
MODULE_ORDER_REFUSED = 0

#: A delivered module appears this many feet above the orderer's reported Y and this far ahead of
#: its facing at request time. I §9.4.3 (the manual's "~100 ft above and ~20 ft ahead"); the height
#: is a server choice.
MODULE_DROP_HEIGHT = 100
MODULE_DROP_AHEAD_FEET = 20

#: Most jobs (towers, modules, Air Support) a team's guppy may have waiting before a module order
#: is refused with ok = 0 or Air Support is not queued. A safety bound only: a player orders at most
#: once a second (``RELAY_LIMIT_ORDER``), so honest play never nears it. Ours (DESIGN §5).
MAX_WAITING_JOBS = 32

#: Most dropped modules a city keeps; past it the oldest is destroyed. A drop lasts until picked up
#: (I §9.3.3), so without a bound a client ejecting at its rate limit would grow the city forever.
#: Ours (DESIGN §5).
MAX_DROPS = 200

#: An ejected module appears this many feet above the pod's last reported position with vertical
#: velocity ``EJECT_VY`` and a horizontal kick of one foot per frame, cycling through these four
#: ``(vx, vz)`` directions: about 18 frames in the air, ~18 ft of travel, inside the 50 ft pickup
#: radius. A server choice (a guess).
EJECT_HEIGHT = 15
EJECT_VY = 1
EJECT_KICKS = ((1, 0), (0, 1), (-1, 0), (0, -1))

#: The client enforces a 50 ft pickup radius itself; a request from further away is logged, not
#: refused. I §9.4.1, I §9.3.3.
PICKUP_RANGE_FEET = 50

#: Weight reports that may disagree with the installed-module ledger after a change before it counts
#: as a mismatch, and reports that must match without a pending install before it is dropped as
#: "never landed". The client's next ``'b'`` can have been built before the change landed. I §9.3.4,
#: II §13; a guess that the ledger replay passes at.
LOADOUT_GRACE_REPORTS = 2

#: Per-session limit on pickup and eject requests, a token bucket ``(burst, per second)``, as
#: ``RELAY_LIMIT_*`` (DESIGN §5): an eject spawns a ``'P'`` and a pickup a ``'Q'`` destroy for every
#: other client. A pod has nine bays, so the burst covers ejecting them all. Ours.
RELAY_LIMIT_MODULE_OPS = (12, 4.0)

#: "Within one block" for the effect modules: within this many feet, straight-line across the
#: ground, of the sender's last reported position, measured to each tower's or mine's ordered point
#: (the client never reports where one comes to rest). The manual says only "within one block";
#: a block is 600 ft. I §9.3.5, II §16.1; a guess.
ONE_BLOCK_FEET = 600

#: How long a tower stays immune to Muncher attack after Immunize. I §9.3.5, II §16.1 (the manual's
#: 30 minutes); M9's Muncher consults it.
IMMUNIZE_SECONDS = 1800.0

#: Most mines a city holds; past it the oldest is destroyed. A mine lasts until swept (I §9.3.5),
#: so without a bound a client sending ``'E'``-``'M'`` at its rate limit would grow the city
#: forever. Ours (DESIGN §5).
MAX_MINES = 200

#: Per-session limit on effect requests and Air Support, a token bucket ``(burst, per second)``, as
#: ``RELAY_LIMIT_*`` (DESIGN §5): each can broadcast (``'J'``, ``'F'``, ``'R'``) or queue a guppy
#: job. A one-shot module is used up per request, so honest play is far slower. Ours.
RELAY_LIMIT_EFFECT = (6, 2.0)

#: The ``'E'``-``'A'`` reply code: 0 = "GUPPY: Rgr, Air Support. CLEAR AREA!". I §9.3.5, II §16.1.
AIR_SUPPORT_ACKNOWLEDGED = 0

#: Air Support strikes kept for M10's resume check (enemy strikes near a dropped pod). Ours.
STRIKE_LOG_MAX = 64

# --- The living city: weather, Sentinels, Munchers (I §8.20, I §9.5.2, II §16.2, II §16.2, II §5)
# ----------

#: The client's palette crossfade takes about 50 s, and a crossfade started during another back to
#: the pre-fade palette is dropped, so palette changes must be at least this far apart (I §8.20,
#: observed 2026-10-16). The client's number.
CROSSFADE_SECONDS = 50.0

#: Our margin on top of the crossfade, and the least gap between two light changes in a city's
#: program, wrap included (II §5; the 5 s
#: margin is ours).
FADE_MARGIN_S = 5.0
MIN_LIGHT_GAP_S = CROSSFADE_SECONDS + FADE_MARGIN_S

#: Shortest interval between two spawns of one timer (``sentinel_respawn``, ``muncher_every``), and
#: shortest total period of a weather program. The timers catch up in a loop, so a typo such as
#: 0.000001 would run it a million times per tick and freeze every city (DESIGN §3.3). Ours
#: (DESIGN §5): a bound on a configuration file, not a rule. ``muncher_every`` may also be 0 (off).
MIN_SPAWN_INTERVAL = 1.0
MIN_AMBIENT_PERIOD = 5.0

#: Most steps a weather program may have, and the longest delay of a step, in seconds (a week).
#: Ours (DESIGN §5): bounds on a configuration file, not on any rule.
MAX_AMBIENT_STEPS = 64
MAX_AMBIENT_AFTER = 7 * 24 * 3600.0

#: Most resident Sentinels a city may ask for: one per block outside the four home corners' 3x3
#: tile areas (64 blocks - 4 x 4 tiles on the map). II §16.3 ("one per block").
MAX_SENTINELS = 48

#: Seconds before a destroyed resident Sentinel is replaced, when a city sets none. Ours (the
#: shipped B value; II §16). The longest a configuration may set is a day.
DEFAULT_SENTINEL_RESPAWN = 300.0
MAX_SPAWN_INTERVAL = 24 * 3600.0

#: A Sentinel's durability, called or resident, on the client's per-shot table; destroyed with
#: ``'J'`` at 0. The client holds none, so the number is ours: a full pod's power, as towers
#: (II §16.3, II §10).
SENTINEL_HP = 3000

#: A resident Sentinel is placed this far into its block, along each axis, at random between the
#: two. II §16 ("150-450 ft into the block").
SENTINEL_OFFSET_FEET = (150, 450)

#: Most called Sentinels a city holds; past it the oldest is destroyed. A called one is not replaced
#: and lasts until shot down (II §16), so without a bound a client sending ``'E'``-``'S'``
#: at its rate limit would grow the city forever. Ours (DESIGN §5).
MAX_CALLED_SENTINELS = 16

#: Seconds a Muncher stays latched on a tower before the tower is destroyed. The client times no
#: meal (its latched state just waits), so the number is ours (II §16.2).
MUNCHER_EAT_SECONDS = 15.0

#: A Muncher's durability on the client's per-shot table, as towers and Sentinels: "can be
#: destroyed with weapons" (manual), the number ours (I §9.5.2, II §16.2).
MUNCHER_HP = 3000

#: How far from itself a Muncher looks for its next tower, in feet, straight-line across the
#: ground: the manual's "within one block" (II §16.2).
MUNCHER_HUNT_FEET = 600

#: A Muncher with nothing left to eat flies to the nearest city edge, to a point this many feet
#: inside it, and is removed on arrival (II §16.2).
MUNCHER_EDGE_INSET_FEET = 50

#: Most Munchers in a city at once; a Call Muncher past it is ignored. Ours (DESIGN §5): the module
#: is used up per request, so honest play never nears it, but each Muncher costs legs of traffic.
MAX_MUNCHERS = 16
