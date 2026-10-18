# OpenCS

A server compatible with the 1993 online game *CyberStrike*, so the original DOS client can be played
again over TCP.

*CyberStrike* is a trademark of Simutronics Corp. This project is not affiliated with or endorsed by
Simutronics, and contains none of the game's files.

## What it is

OpenCS is a Python server. The unmodified DOS client, run in dosbox-x with its serial port bridged to
TCP, logs in, chats in the Chamber (the lobby), joins a city and plays: pods, teams, combat, bounty and
scoring, towers delivered by guppies, modules, weather, Sentinels and Munchers, saved progress and
`/RESUME` after a dropped line. It is built from the protocol specification
([spec v1.0](https://github.com/mmq/cyberstrike-spec)) and needs only the Python standard library at run time.

## Status

**Release candidate 1.0.0rc1.** All milestones M0 to M13 are built and the black-box judge passes all 19
checks. What a real client has and has not been seen to do is still being
confirmed; `1.0.0` follows the owner's live rounds, including a game
between two networks. See [`CHANGELOG.md`](CHANGELOG.md).

## Quick start

**Operators** (Python 3.12 or newer, [`uv`](https://docs.astral.sh/uv/)):

```bash
uv sync                      # create .venv with the dev tools
uv run python -m opencs      # start the server (config in config/; port 2399)
tools/smoke.sh               # start a throwaway server and run the 19 judge checks (about a minute)
uv run pytest                # tests
```

To install on a server, run it as a service and open it to players on another network, read
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md); to run, configure, watch and back it up, read
[`docs/operations.md`](docs/operations.md).

**Players** need their own copy of the game, dosbox-x and a server's address. Read
[`docs/PLAYING.md`](docs/PLAYING.md): the dosbox-x serial setup (8N1), the phonebook entry, logging in as
a guest or with an account, the Chamber commands and troubleshooting.

## Documentation

| file | what |
|---|---|
| [`docs/PLAYING.md`](docs/PLAYING.md) | connecting the game and playing |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | installing, running as a service, ports, backups, two networks |
| [`docs/operations.md`](docs/operations.md) | running, configuring, logs, capture, the admin channel, upgrading |
| [`docs/robustness.md`](docs/robustness.md) | every input path and the limit that bounds it |
| [`docs/DESIGN.md`](docs/DESIGN.md) | layers, package layout, conventions |
| [Spec v1.0](https://github.com/mmq/cyberstrike-spec) | the protocol and server rules (the `cyberstrike-spec` repository) |
| [`docs/decisions/`](docs/decisions/README.md) | architecture decision records |
| [`CHANGELOG.md`](CHANGELOG.md) | the milestones, newest first |

## License and notice

Licensed under the MIT License (`LICENSE`). OpenCS is the name of this project. *CyberStrike* is named
only to say which game it is compatible with; the game's files, art, text and manual are not part of
this repository and are never distributed by it.
