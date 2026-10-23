# OpenCS

OpenCS is a server for *CyberStrike*, the online game Simutronics launched on GEnie in 1993 and later
ran on America Online and play.net. The original GEnie DOS client connects to it unchanged, so the game
can be played again over the internet. It implements the [CyberStrike Server
Specification](https://github.com/mmq/cyberstrike-spec), written alongside it from the original client
and the game's manual.

*CyberStrike* is a trademark of Simutronics Corp. This project is not affiliated with or endorsed by
Simutronics, and contains none of the game's files.

## What it is

OpenCS lets the unmodified DOS client log on and dive into the action like it's 1993 again:

- the Chamber, the game's lobby, for chatting and picking a game;
- cities of up to 16 pilots on four teams;
- pods, shots, hits, bounty and scoring, deaths and respawns;
- towers flown in by each team's guppy, territory, and the full module catalog;
- weather, Sentinels and Munchers;
- accounts with saved progress, and `/RESUME` to get back into a game after a dropped line.

## How to play

You need your own copy of the game, the address of a server, and a way to run the game with a modem that
dials out over the internet: [DOSBox-X](https://dosbox-x.com/),
[DOSBox Staging](https://www.dosbox-staging.org/), or any DOS setup with an emulated modem. In short:

1. In DOSBox-X or DOSBox Staging, set the first serial port to a modem (`serial1=modem`) and point
   `phonebookfile` at a phonebook file.
2. In the phonebook, map the number the game dials to the server: `1234 host.example.org:2399`, or
   `1111 localhost:2399` for a server on your own machine.
3. In the game's SETTINGS, set that dial number, a baud rate of 9600, and an account name and password
   (or leave the stock guest name `JOINGENIE`).
4. Log on, open the Chamber and type `/PLAY`, or `/PLAY B RED` to pick a city and a team.

The full walk-through, with every Chamber command and troubleshooting, is
[`docs/PLAYING.md`](docs/PLAYING.md).

## How to run

Python 3.12 or newer and [`uv`](https://docs.astral.sh/uv/):

```bash
uv sync                      # create .venv with the dev tools
uv run python -m opencs      # start the server (config in config/; port 2399)
tools/smoke.sh               # start a throwaway server and run the 19 judge checks (about a minute)
```

Installing on a server, running it as a service, configuring cities, the admin channel, backups and
upgrades are in the operator guide, [`docs/OPERATING.md`](docs/OPERATING.md).

## Architecture in brief

The code is a stack of layers, and no layer imports one above it:

| layer | package | does |
|---|---|---|
| wire | `opencs.wire` | number codecs, frames, the Chamber's line reader |
| messages | `opencs.messages` | one typed class per protocol message (34 server, 24 client) |
| net | `opencs.net` | the asyncio listener, one connection per socket, bounded outboxes |
| session | `opencs.session` | the connection's phases: logon, Chamber, game |
| lobby | `opencs.lobby` | who is where, the Chamber commands, teams and callsigns |
| world | `opencs.world` | the game rules: one `City` per game |
| routing | `opencs.routing` | turns an event's audience into the sessions that receive it |
| store | `opencs.store` | accounts and progress behind small interfaces, with SQLite behind them |

The game rules (`opencs.world`) are separate from the networking code, so they can be tested without a
network. The server runs on a single asyncio event loop.

The server's built-in settings (the limits, timeouts and game numbers the protocol leaves up to the
server) are in `src/opencs/policy.py`, each noting where it comes from. Some, such as the port, logging,
connection and account limits and each city's capacity and teams, can be changed in `config/server.toml`
or a city's file; the rest are fixed.

[`docs/DESIGN.md`](docs/DESIGN.md) has the layers, the package layout and the conventions. The reasoning
behind the main choices is in [`docs/decisions/`](docs/decisions/README.md), one record each:
standard library only, the world returns events, one loop and no locks, audiences as data, tags at the
send boundary, centralized policy, SQLite behind protocols, and written from the spec.
[`docs/robustness.md`](docs/robustness.md) lists every input path and the limit that bounds it.

## Testing

```bash
uv run pytest                # more than 2,500 tests, under a minute
uv run ruff check . && uv run ruff format --check .
uv run mypy                  # strict, on src/
```

| level | what |
|---|---|
| unit | codecs and frames against the spec's own hex examples, every message round-tripped, every rule module with a manual clock |
| replay | traffic captured from real client sessions (deaths, guppy legs, Muncher trips) driven through the world |
| fuzz and long run | random and hostile input against the net and session layers; a simulated 48-hour city |
| end to end | a real server on loopback, and the judge: two scripted pilots through the Chamber and a game, 19 checks (`tools/smoke.sh`, or `pytest -m judge`) |

A server can also capture any connection's wire traffic in the replay format, so a bug seen in a live
game becomes a test (see the operator guide).

## The specification

OpenCS implements the **CyberStrike Server Specification, version 1.0**, published in the
[`cyberstrike-spec`](https://github.com/mmq/cyberstrike-spec) repository. It has four parts:

| part | document | is |
|---|---|---|
| I | `protocol.md` | what the client does and requires: transport, every message, the game rules the client implements |
| II | `server-policy.md` | what the client leaves up to the server, with a suggested way to handle each (OpenCS 1.0 is based on it) |
| III | `implementers-guide.md` | advice for building a server |
| IV | `city-format.md` | the format of the game's city map files, for making new cities (a server doesn't need it) |

Code, tests and docs cite it as **`I §n`**, **`II §n`**, **`III §n`** or **`IV §n`**: the part, then the
section number. `I §4.1` is section 4.1 of the protocol (framing), `II §13` is section 13 of the server
policy (holding a game for `/RESUME`). A behavior that is not traced to a section is a choice recorded in
`policy.py`.

## Documentation

| file | what |
|---|---|
| [`docs/PLAYING.md`](docs/PLAYING.md) | connecting the game and playing |
| [`docs/OPERATING.md`](docs/OPERATING.md) | installing, running as a service, configuring, logs, the admin channel, backups, upgrading |
| [`docs/DESIGN.md`](docs/DESIGN.md) | layers, package layout, conventions |
| [`docs/decisions/`](docs/decisions/README.md) | architecture decision records |
| [`docs/robustness.md`](docs/robustness.md) | every input path and the limit that bounds it |
| [`CHANGELOG.md`](CHANGELOG.md) | what changed for players and operators |

## License and notice

Licensed under the MIT License ([`LICENSE`](LICENSE)). OpenCS is the name of this project. *CyberStrike*
is named only to say which game it is compatible with; none of the game's files (its program, maps or
manual) are part of this repository or distributed by it.
