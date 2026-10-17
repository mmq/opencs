# OpenCS

A server compatible with the 1993 online game *CyberStrike*, so the original DOS client can be played
again over TCP. Under construction.

*CyberStrike* is a trademark of Simutronics Corp. This project is not affiliated with or endorsed by
Simutronics, and contains none of the game's files.

## What it is

OpenCS is a Python server for the 1993 online game *CyberStrike*. The unmodified DOS client, run in
dosbox-x with its serial port bridged to TCP, logs in, chats in the lobby and plays. It is built from the
protocol specification ([spec v1.0](https://github.com/mmq/cyberstrike-spec)), uses only the Python standard library at runtime, and
is written one milestone at a time.

## Status

Milestone M0: the project skeleton. The server starts a TCP listener and stops cleanly; the protocol
itself arrives in later milestones.

## Running

```bash
uv sync                      # create .venv with the dev tools
uv run python -m opencs      # start the server (config in config/)
uv run pytest                # tests
```

## Documentation

- [`docs/DESIGN.md`](docs/DESIGN.md): layers, package layout, conventions
- [Spec v1.0](https://github.com/mmq/cyberstrike-spec): the protocol and server rules
- [`docs/decisions/`](docs/decisions/README.md): architecture decision records

Licensed under the MIT License (`LICENSE`).
