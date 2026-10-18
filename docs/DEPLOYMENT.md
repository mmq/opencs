# Deploying OpenCS

From a fresh machine to a server other people can play on. Day-to-day running, logs, the admin channel,
backups and reloading are in `operations.md`; what players do is in `PLAYING.md`.

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
sudo uv sync --no-dev                  # creates /opt/opencs/.venv with the project installed
/opt/opencs/.venv/bin/python -m opencs --version     # opencs 1.0.0rc1
```

Check it works before wiring a service: `sudo tools/smoke.sh` starts a throwaway server on a free port
with a throwaway data directory, plays the 19 judge checks against it (about a minute) and prints
`smoke: ok (19 of 19 checks passed)`. Run it again after every upgrade. (It uses the
`.venv` it finds in the checkout.)

## Layout

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

# Optional: the operator channel, on 127.0.0.1 only.
[admin]
port = 2400
```

If you use the admin channel, put a token (at least 16 characters) in the environment file:

```bash
sudo sh -c 'umask 077; echo "OPENCS_ADMIN_TOKEN=$(head -c 24 /dev/urandom | base64 | tr -d "=+/")" > /etc/opencs/environment'
```

Every other key, its default and limits are in `operations.md` ("Configuration"). The cities (letter, map,
multiplier ceiling, tower output, module level, Sentinels, weather) are the files in
`/etc/opencs/cities/`; a map number needs `C<n>` on each player's PC, and only 1 to 3 come with the game.

## Run as a service

Use the unit in `operations.md` ("systemd"): copy it to `/etc/systemd/system/opencs.service`, then

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now opencs
sudo systemctl status opencs
journalctl -u opencs -f              # the start-up line "listening on HOST:PORT"
sudo systemctl reload opencs         # re-read the city files after editing them
sudo systemctl stop opencs           # graceful: players are saved
```

Exit codes help when it will not start: 1 means a start-up step failed (the message names the database
or its directory, the game port or the admin port), 2 means a bad config file, an unwritable log file or
an admin port without a usable token.

## Ports and firewall

| port | default | exposure |
|---|---|---|
| game | TCP 2399 (`port`) | **public**: open and forward this one |
| admin | TCP 2400 if `[admin] port` is set | **127.0.0.1 only**: never open or forward it |

```bash
sudo ufw allow 2399/tcp
sudo ufw status
```

The server accepts 8 connections per address and 64 in all (`MAX_CONNECTIONS_PER_ADDRESS`,
`MAX_CONNECTIONS` in `src/opencs/policy.py`). The link is plain TCP carrying the game's own protocol with
no encryption: a password travels in the clear, so tell players to use one they use nowhere else.

## Data and backups

All state is the SQLite file `/var/lib/opencs/opencs.db`. Back it up while the server runs with SQLite's
tool, never `cp`:

```bash
sudo -u opencs sqlite3 /var/lib/opencs/opencs.db ".backup '/var/backups/opencs-$(date +%F).db'"
```

Run that daily from cron or a systemd timer, and copy the result off the machine. Restore by stopping the
service and putting a backup in place of `opencs.db` (owned by `opencs`). Games held for `/RESUME`, the
cities' worlds and login limits are in memory and are lost on a restart: announce one
(`shutdown 120` on the admin channel) so players finish first.

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

## Upgrading

As in `operations.md`: `git pull` and `sudo uv sync --no-dev` in `/opt/opencs`, back up `opencs.db`,
announce and stop (`shutdown 120`, or `systemctl stop opencs`), start, read the start-up log for config
errors, run `tools/smoke.sh`. The database moves forward on open and refuses a newer one; to go back,
restore the backup you took.

## Checklist: a game between two networks

For players who are not on the same LAN as the server (or each other):

1. **Server host.** `sudo tools/smoke.sh` passes; `systemctl status opencs` is active; the log shows
   `listening on 0.0.0.0:2399`.
2. **Host firewall.** `sudo ufw allow 2399/tcp` (or the equivalent). Admin port stays closed.
3. **Router/NAT.** Forward TCP 2399 on the router's public address to the host's LAN address and fixed
   port. If the host is behind carrier-grade NAT (no public address), forward through a VPS or use a
   tunnel (for example `ssh -R`), because the players need an address that reaches the host.
4. **Address.** Note the public IP or a DNS name for the host. From a machine on another network:
   `nc -vz <address> 2399` must report success. Remember a home connection's address may change.
5. **Each player** (from `PLAYING.md`):
   - their own copy of the game and DOSBox-X or DOSBox Staging;
   - `serial1=modem` and a `phonebookfile` in the emulator's config;
   - a phonebook line `<number> <address>:2399`, and that number as the dial number in `CS.CFG`;
   - an account name and a password in `CS.CFG` (or `JOINGENIE` for a guest), and the same account name
     and password on every login;
   - the map files `C1` to `C3` (they come with the game) unless the operator added others.
6. **Everyone: team and city.** Agree a city letter and a team color; each types
   `/PLAY <letter> <team>` (teams hold 4 each, `/CITY <letter>` shows who is where).
7. **Confirm.** `/WHO` shows both pilots; once in a game both see the other's pod; chat appears on both;
   the latency meter shows a number on both.
8. **Players behind one address** share the 8-connections-per-address limit; for a household of more
   than 8 raise `MAX_CONNECTIONS_PER_ADDRESS` in `policy.py`.
