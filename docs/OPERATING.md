# Operating OpenCS

The operator's guide: install the server on a machine, run it as a service, open it to players, configure
it, watch it, back it up and upgrade it. What players do is in the player guide ([`PLAYING.md`](PLAYING.md));
how the code is organized is in [`DESIGN.md`](DESIGN.md). Every limit named here is a value in
`src/opencs/policy.py`, with its reason beside it.

## Install on Ubuntu 24.04

Ubuntu 24.04 ships Python 3.12, which is what OpenCS needs. At run time it uses only the standard
library; `uv` is used to create the virtual environment.

```bash
sudo apt update
sudo apt install -y git sqlite3 curl          # sqlite3: for the online backup command

# uv, installed for everyone
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_UNMANAGED_INSTALL=/usr/local/bin sh

# the server, in /opt/opencs
sudo git clone <the repository URL> /opt/opencs
cd /opt/opencs
sudo uv sync --no-dev --python /usr/bin/python3.12   # makes /opt/opencs/.venv
/opt/opencs/.venv/bin/python -m opencs --version     # opencs 1.0.0rc1
```

Check that it works before wiring a service: `sudo tools/smoke.sh` starts a throwaway server on a free
port with a throwaway data directory, plays the 19 judge checks against it (about a minute) and prints
`smoke: ok (19 of 19 checks passed)`. Run it again after every upgrade. (It uses the `.venv` it finds in
the checkout.)

### Layout

| path | holds |
|---|---|
| `/opt/opencs` | the code and its `.venv` (read-only to the service) |
| `/etc/opencs/server.toml`, `/etc/opencs/cities/*.toml` | the configuration |
| `/etc/opencs/environment` | `OPENCS_ADMIN_TOKEN=...`, mode 0600, only if the admin channel is used |
| `/var/lib/opencs` | the data directory: `opencs.db` (accounts and progress) |
| `/var/log/opencs` | the log file, if `[logging] file` is set |
| `/var/lib/opencs-captures` | session captures, if `[capture]` is enabled |

```bash
sudo useradd --system --home /var/lib/opencs --shell /usr/sbin/nologin opencs
sudo install -d -o opencs -g opencs /var/lib/opencs /var/log/opencs /var/lib/opencs-captures
sudo install -d /etc/opencs
sudo cp -r /opt/opencs/config/. /etc/opencs/
```

Edit `/etc/opencs/server.toml`. Paths in it are relative to the file, and `/etc` is read-only to the
service, so point the writable things elsewhere, with absolute paths or on the command line:

```toml
port = 2399
data_dir = "/var/lib/opencs"      # the unit also passes --data-dir

[logging]
file = "/var/log/opencs/opencs.log"

[capture]
dir = "/var/lib/opencs-captures"   # only used if capture is enabled

# Optional: the operator channel, on 127.0.0.1 only.
[admin]
port = 2400
```

If you use the admin channel, put a token (at least 16 characters) in the environment file:

```bash
sudo sh -c 'umask 077; echo "OPENCS_ADMIN_TOKEN=$(head -c 24 /dev/urandom | base64 | tr -d "=+/")" > /etc/opencs/environment'
```

Every other key, its default and its limits are under "Configuration" below. The cities (letter, map,
multiplier ceiling, tower output, module level, Sentinels, weather) are the files in
`/etc/opencs/cities/`; a map number needs `C<n>` on each player's PC, and only 1 to 3 come with the game.

## Running

OpenCS needs Python 3.12 or newer and nothing else at run time (the standard library only).

```bash
uv sync                          # once: creates .venv
uv run python -m opencs          # config/server.toml, state in ../data relative to it
uv run python -m opencs --config /etc/opencs/server.toml --port 2399 --data-dir /var/lib/opencs
```

Command-line options override the file: `--config`, `--host`, `--port`, `--data-dir`. Exit codes: 0 after
a clean stop; 1 when a startup step fails (the message names it: the database or its directory, the game
port, the admin port); 2 for a bad config file, an unwritable log file, or an admin port with no usable
token.

### As a service (systemd)

```ini
# /etc/systemd/system/opencs.service
[Unit]
Description=OpenCS game server
After=network.target

[Service]
User=opencs
WorkingDirectory=/opt/opencs
# The admin token is a secret: keep it out of server.toml and out of the process list.
# The file holds OPENCS_ADMIN_TOKEN=<at least 16 characters>.
EnvironmentFile=-/etc/opencs/environment
ExecStart=/opt/opencs/.venv/bin/python -m opencs --config /etc/opencs/server.toml --data-dir /var/lib/opencs
# Re-read the city files.
ExecReload=/bin/kill -HUP $MAINPID
# A graceful stop: players are saved. The timeout is more than the 10 s shutdown deadline.
KillSignal=SIGTERM
TimeoutStopSec=30
Restart=on-failure
ProtectSystem=strict
ReadWritePaths=/var/lib/opencs /var/log/opencs /var/lib/opencs-captures
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

The paths in `server.toml` are relative to the file, and `/etc` is read-only under `ProtectSystem=strict`.
`--data-dir` puts the database in a writable place; the log file and capture directory take absolute paths
that match `ReadWritePaths` (as in the configuration above).

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now opencs
sudo systemctl status opencs
journalctl -u opencs -f              # the start-up line "listening on HOST:PORT"
sudo systemctl reload opencs         # re-read the city files after editing them
sudo systemctl stop opencs           # graceful: players are saved
```

### Ports and firewall

| port | default | who | note |
|---|---|---|---|
| game | TCP 2399 (`port`) | **public** | what the players' games connect to; open and forward this one |
| admin | none (`[admin] port`; 2400 in the example) | **this machine only** | bound to `127.0.0.1` whatever the config says; never open or forward it |

```bash
sudo ufw allow 2399/tcp
sudo ufw status
```

The game listener limits connections to 64 in total and 8 per address (`MAX_CONNECTIONS`,
`MAX_CONNECTIONS_PER_ADDRESS`); the rest are closed at once. Behind a NAT or a proxy that makes every
player share one address, raise the per-address limit in `policy.py`. The link is plain TCP carrying the
game's own protocol with no encryption: a password travels in the clear, so tell players to use one they
use nowhere else.

### Players behind one address

Players who share one public address, such as a household or a LAN party behind one router, share the
per-address limits: 4 connections, 2 not yet logged in and 2 guests. For them, raise
`max_connections_per_address`, `max_connections_per_subnet`, `max_unauthenticated_per_address` and
`max_guests_per_address` under `[limits]` in `server.toml`.

## Configuration

All paths in `server.toml` are relative to the file. An unknown key, a wrong type or an out-of-range
value is an error that names the file and the key, and nothing starts. `server.toml` is read once at
start; **only the city files are re-read by `reload`** (below).

### `server.toml`

| key | default | limits | meaning |
|---|---|---|---|
| `host` | `"0.0.0.0"` | an address | where the game port listens |
| `port` | `2399` | 0-65535 (0 = a free port) | the game port |
| `data_dir` | `"../data"` | a directory, created | holds `opencs.db` (accounts and progress) |
| `cities_dir` | `"cities"` | a directory | one `*.toml` per city |
| `default_city` | `"B"` | a loaded city | what a bare `/PLAY` picks when every city is empty |
| `guest_names` | `["JOINGENIE"]` | names; `[]` turns guests off | the guest names (II §3): no password check, the only names several players may share, never saved |
| `[logging] level` | `"INFO"` | DEBUG, INFO, WARNING, ERROR, CRITICAL | for everything the server logs |
| `[logging.levels] <layer>` | none | a layer below; a level | overrides `level` for `opencs.<layer>` |
| `[logging] file` | none (stderr only) | a path | also log to this file, rotating |
| `[logging] max_bytes` | `5000000` | 1 to 1e9 | rotate the file at this size |
| `[logging] backups` | `5` | 0-100 | old files kept |
| `[logging] format` | `"%(asctime)s %(levelname)s %(name)s: %(message)s"` | a Python logging format | the line layout |
| `[capture] enabled` | `false` | boolean | record each connection's wire traffic |
| `[capture] dir` | `"../captures"` | a directory, created | one file per connection |
| `[capture] callsigns` | `[]` (everyone) | upper-case callsigns | capture only these pilots |
| `[capture] max_bytes` | `4000000` | 1000 to 1e9 | a capture stops at this size |
| `[admin] port` | none (off) | 0-65535, not the game port | the admin channel's port |
| `[admin] token` | none | at least 16 characters | the admin token, in the file |
| `[admin] token_env` | `"OPENCS_ADMIN_TOKEN"` | an environment variable name | where the token is read when `token` is absent |

Layers for `[logging.levels]`: `wire`, `messages`, `net`, `session`, `lobby`, `world`, `routing`, `store`,
`app`, `admin`, `capture`. Every logger in the code is `opencs.<layer>[.<detail>]`, so a level set for a
layer reaches its children.

### A city file (`cities/*.toml`)

| key | default | limits | meaning |
|---|---|---|---|
| `letter` | required | one of A-Z, unique | the city's key (`/PLAY B`) |
| `name` | required | 1-16 printable ASCII | shown in the lobby and the banner |
| `map` | required | 1-9 | the map file the client loads (any number of cities may share one) |
| `max_multiplier` | required | 1-51 | the multiplier ceiling (I §9.2.5); with `blocks` the income product must stay under the client's 16-bit limit |
| `tower_output` | required | 0 Low, 1 Normal, 2 High | |
| `max_module_level` | required | 0-5 | highest module level stocked |
| `capacity` | `16` | 1-16 | players in the city |
| `team_policy` | `"request-or-balance"` | `request-or-balance`, `balance`, `random` | how `/PLAY` picks a team |
| `balance_teams` | `[0,1,2,3]` | non-empty, no repeats, 0-3 | teams the balancer may fill |
| `banned_modules` | `[]` | catalog indices 1-47 | refused as "no X modules in stock" |
| `enabled` | `true` | boolean | `false` is the same as no file |
| `blocks` | all class 1 | 8 strings of 8 digits 0-3 (rows are Z, columns X) | territory worth 1/2/5/10 |
| `sentinels` | `0` | 0-48 | resident Sentinels |
| `sentinel_respawn` | `300` | 1 to 86400 s | seconds before a destroyed one is replaced |
| `muncher_every` | `0` (none) | 0, or 1 to 86400 s | seconds between free Munchers |
| `[[ambient]]` | none | up to 64 steps; total period at least 5 s; light changes at least 55 s apart, the wrap included | the weather program: `after` seconds, then `light` 0-4, `rain` 0-3, `lightning` |

The shipped `config/cities/` is the reference: A (training, no NPCs), B, C (the table in II §5).

## Logs

Lines go to stderr (journald under systemd) and, with `[logging] file`, to a rotating file. A line is
`time level logger: [conn=N CALLSIGN] message`. `conn` is the connection's id (from 1 at each start) and
the callsign is `-` until the logon completes. Useful ones at INFO: accept and close, logon and logout,
phase changes, `seated as player N`, `line dropped; held for /RESUME`, `closing: <reason>`. A bug is
always an `ERROR` with a traceback and the request that triggered it; the connection or city carries on
(DESIGN §5), so grep for `ERROR` and `Traceback`.

Passwords, and the admin token, are never logged (tests pin both). Callsigns and addresses are.

## Capturing a session

Set `[capture] enabled = true` (optionally `callsigns = ["ALPHA"]`) and restart. Each connection writes
`<capture dir>/<YYYY-MM-DD-HHMMSS>_<conn>-<CALLSIGN>.txt` in the fixture format of
`tests/fixtures/README.md`:

```
# source: opencs connection 3 (ALPHA)
12.340 C <hex>     a whole game frame from the client
12.341 S <hex>     a whole game frame to the client, with its terminator and the tag it was sent with
12.000 c <hex>     Chamber text from the client (one read)
12.001 s <hex>     Chamber text to the client
```

Times are seconds since the connection began. What the client types at the GEnie logon (the account
line, with its password) is **never** recorded; the server's own prompts may appear. A file stops with a
`# truncated` line at `max_bytes`; a file that cannot be written ends that capture and is logged, the
connection carries on. The directory as a whole is capped at 200 MB (`CAPTURE_DIR_MAX_BYTES`): before a
new file is made the oldest capture files are removed (with a log line) until a full one fits; only files
named as captures are touched, so a fixture you keep there is safe, but copy what you want to keep
elsewhere. Capturing only the callsigns you are chasing (`callsigns`) keeps the history longer. Captures hold chat and callsigns: treat them as private.

To make a bug into a test: copy the file into `tests/fixtures/captures/`, load it with
`fixtures.captures.load_capture_file`, and replay it through a fresh city the way
`tests/replay/test_live_capture_replay.py` does (every frame must decode and the city must answer the
client's messages with the same server messages in order). `2026-10-18-120000_001-live-loopback-p1` is a
capture of the server's own, committed as the example. Remove anything you do not want in the repository
(callsigns, chat) before committing one.

## The admin channel

Off unless `[admin] port` is set. It needs a token: `[admin] token` in the file, or (better) the
environment variable `OPENCS_ADMIN_TOKEN` (or the name in `token_env`). A token shorter than 16
characters, or none, stops the server at start. It listens on **127.0.0.1 only** and refuses any peer that
is not loopback; reach it over SSH (`ssh -L 2400:127.0.0.1:2400 host`) if you are not on the machine.

```bash
nc 127.0.0.1 2400
opencs admin: send the token
<paste the token>
ok

status
uptime: 3725 s
...
```

The first line must be the token (compared in constant time; a wrong one is denied and the connection
closed; five attempts in a burst across all connections, then one per ten seconds). After `ok` each line
is a command and each reply ends with an empty line. Bounds: lines of at most 512 bytes, 20 commands
burst and 5 a second, 10 minutes of silence, 4 connections at once; any breach closes the connection.

Trade-off: the token-attempt limit is shared by every connection, and the four connection slots are
taken before a client has authenticated (for up to 10 seconds each). Any local user can therefore make
the channel refuse the operator for a while by connecting or guessing in a loop. That is accepted: it
costs the attacker a local account and denies service only to the channel (the game is unaffected),
while slowing token guessing is what matters. If it happens, find the process (`ss -tnp` on the admin
port) or restart the server, which resets the limits.

| command | does |
|---|---|
| `status` | uptime; connections by phase (logon, Chamber, game); per city its players by team (and `[closing: no new players]`); held `/RESUME` games with the time left |
| `announce <text>` | a system line (`'a'`, sender 100) on every started in-game client and a `*** text` line to each Chamber pilot. Text is made printable ASCII and cut to 230 characters. Says how many received it |
| `kick <callsign> [reason]` | in a game: the reason as a system line, then an unprompted `'C'` (I §5.5, I §8.5): the client returns to the Chamber exactly as after its own quit, where the reason is shown again. In the Chamber: a line, and the connection is closed; at the logon, just closed (no line is sent). See "Kicks and shutdown" |
| `guppy <team> home` | the failsafe for a stranded team guppy (below); `<team>` is RED, BLUE, GREEN, BLACK or 0-3; applies in every city that has that team's guppy |
| `shutdown [seconds]` | with no argument, stops now; with seconds (at most 3600), announces and stops then. `shutdown cancel` withdraws a pending one |
| `reload` | re-read the city files (below) |
| `help`, `quit` | |

### The stranded guppy

A modified client reporting a guppy's legs early can leave a team's guppy waiting for ever (our leg-report rules; see I §9.4.1). Symptom: a team's tower orders are accepted
(`'O'` 0) but nothing ever lands, and `status` shows nothing wrong. `guppy RED home` teleports that
team's guppy to its corner and starts its count again; the interrupted order is flown again first and
the queue follows in order. Do not repeat it within seconds (five `'L'` in a row make the client print
`GUPPY MALFUNCTION`).

One known window: a client's arrival report carries no leg number, so a report for the *old* leg that
was already on its way when the warp was sent cannot be told from the warp's own arrival. The server
then counts the stale one, and that client can end up one leg out of step again. If a team's orders
are still stranded some seconds later, run the command once more (the failsafe is ours; II §14 allows one).

## Stopping, signals and kicks

| signal / command | effect |
|---|---|
| `SIGINT`, `SIGTERM`, admin `shutdown` | graceful stop (below), exit 0 |
| `SIGHUP`, admin `reload` | reload the city files (not on Windows) |

A graceful stop: stop accepting; give every connection the notice `SERVER SHUTTING DOWN` (a game gets a
system line and then the unprompted `'C'`, the Chamber a line; a pilot at the logon is just closed); each
player in a game leaves cleanly, which saves its progress; anyone still seated and any save that failed
earlier is written again; the cities stop ticking; the connections flush and close. Anything still open
after **10 seconds** (`SHUTDOWN_DEADLINE`) is aborted, so a client that never reads cannot hold the stop
up. The SQLite file is closed last.

### Kicks and shutdown

A kick and a shutdown are not the player's doing, so neither is treated as a dropped line: progress is
saved, **no fight test runs** (no death is recorded), and nothing is held for `/RESUME`. A shutdown also
loses every game *already* held for `/RESUME` (they live in memory; see I §5.5 and II §13); a restart should be announced with `shutdown <seconds>` so
players finish what they are doing. A kicked player stays connected and may `/PLAY` again at once.

## Reloading the cities

`reload` (or `SIGHUP`) reads `server.toml` and every city file and **validates all of it first**: any
error changes nothing and the first one is reported. Then, per city:

| the file says | what happens |
|---|---|
| a letter not running | it appears at once |
| a running city, unchanged | nothing |
| a running city, changed, and its game is **empty** | the new configuration takes effect now, with a fresh game |
| a running city, changed, and players are in it (or a `/RESUME` slot is held) | the running game and the old configuration stay; the change is applied after the tick on which the game is next empty |
| a running city removed, or `enabled = false` | it stops taking `/PLAY` at once; it is removed once its game is empty |

A running game is never changed under its players (II §17). A `/PLAY` begun against the old
configuration just before the swap is told "CITY x FILLED UP: TRY AGAIN". A change to `server.toml` (the
port, logging, capture, admin, `default_city`, `guest_names`) needs a restart; a reload says that it saw one. A
config whose `default_city` is missing or has `enabled = false` is refused at validation, at start-up and
on `reload` alike (nothing changes), so the lobby always has a city to offer.


## Backups

State is one SQLite file, `<data_dir>/opencs.db` (accounts with salted PBKDF2 hashes, and progress). It
is written after every progress change, so back it up while the server runs with SQLite's own tool, never
by copying the file:

```bash
sudo sqlite3 /var/lib/opencs/opencs.db ".backup '/var/backups/opencs-$(date +%F).db'"
```

Run it as root: the `opencs` user cannot write to `/var/backups`. Run it daily from cron or a systemd
timer, and copy the result off the machine. Restore by stopping the server and putting a backup in place
of `opencs.db` (owned by `opencs`). `/RESUME` records, the cities' worlds (towers, territory, weather) and
the login rate-limit tables are in memory and are not backed up: a restart starts every city fresh, so
announce one (`shutdown 120` on the admin channel) and players can finish first.

## Upgrading

1. `git pull` and `sudo uv sync --no-dev` in `/opt/opencs`.
2. Back up `opencs.db` (above).
3. `shutdown 120` on the admin channel (players are told once, at the start, that the server stops in 120
   seconds; there is no repeating countdown), or `systemctl stop opencs`.
4. Start the new version. The database schema only moves forward, automatically, on open
   (`MIGRATIONS` is append-only); a database from a newer server is refused rather than guessed at, so
   downgrading means restoring the pre-upgrade backup.
5. Read the start-up log for config errors before announcing the server is back. Config files from an
   older version are accepted if their keys are still known; a removed key is an error that names it.
6. Run `tools/smoke.sh`.

## Two servers on one host

Each server needs its own game port, admin port (if used), data directory and configuration. Do not
share a data directory. With a template unit:

```ini
# /etc/systemd/system/opencs@.service      (instances: opencs@one, opencs@two)
[Unit]
Description=OpenCS game server (%i)
After=network.target

[Service]
User=opencs
WorkingDirectory=/opt/opencs
EnvironmentFile=-/etc/opencs/%i/environment
ExecStart=/opt/opencs/.venv/bin/python -m opencs --config /etc/opencs/%i/server.toml --data-dir /var/lib/opencs-%i
ExecReload=/bin/kill -HUP $MAINPID
KillSignal=SIGTERM
TimeoutStopSec=30
Restart=on-failure
ProtectSystem=strict
ReadWritePaths=/var/lib/opencs-%i /var/log/opencs
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

```bash
for n in one two; do
  sudo install -d /etc/opencs/$n && sudo cp -r /opt/opencs/config/. /etc/opencs/$n/
  sudo install -d -o opencs -g opencs /var/lib/opencs-$n /var/log/opencs
done
# in /etc/opencs/one/server.toml: port = 2399, admin port 2400, log file /var/log/opencs/one.log
# in /etc/opencs/two/server.toml: port = 2499, admin port 2500, log file /var/log/opencs/two.log
sudo systemctl enable --now opencs@one opencs@two
```

Players of each server use the port of theirs in the phonebook. The two do not share accounts, cities or
held games, and a pilot can be online on both at once.
