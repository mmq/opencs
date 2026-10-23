# Changelog

What changed for players and operators, newest first. How the code is built is in
[`docs/DESIGN.md`](docs/DESIGN.md); the reasoning behind its structure is in
[`docs/decisions/`](docs/decisions/README.md).

## 1.0.0rc1 (release candidate)

The first release: a server the unmodified DOS client can log into and play on. `1.0.0` follows real
play testing.

### Players
- Log in as a guest (`JOINGENIE`) or with an account and password, chat in the Chamber and use its
  commands (`/PLAY`, `/CITY`, `/WHO`, `/SCORE`, `/RESUME` and the rest), then join a city on a team.
- A full game: pods, teams, open and team chat, shots and hits, the Grenade, bounty and scoring, deaths and
  respawns, late joiners, and the latency meter.
- Towers flown in by the team's guppy, territory with per-city block maps, and every module the client
  offers: purchase orders, drops, pickup, eject, and the effects Repair, Harden, Immunize, Liberator,
  Weather, Mine, Mine Sweep and Air Support.
- Weather programs, Sentinels that are worn down and replaced, and called and free Munchers.
- Accounts and progress are saved and restored, and `/SCORE` and `/WHO <callsign>` show the record.
  Guests are never saved.
- A dropped line from a real account holds the game for 10 minutes, and `/RESUME` brings it back (3 minutes
  to type it once you are in the Chamber again). A drop within 20 seconds of an attack counts as a death.
- Callsigns are up to 12 characters. A duplicate gets a number from 2 to 999 (`JOINGENIE`, `JOINGENIE2`).
- Three cities (A, a training city with no NPCs; B; C) ship in `config/cities/`.

### Operators
- Configuration in TOML (`config/server.toml` and one file per city), validated at start with errors that
  name the file and key.
- Logging per layer with an optional rotating file; passwords are never logged.
- Optional per-connection wire capture, in the format the tests replay.
- A token-guarded admin channel on 127.0.0.1: `status`, `announce`, `kick`, `shutdown`, `reload`, and a
  failsafe for a stranded guppy.
- Graceful shutdown on SIGINT and SIGTERM that saves every player; `SIGHUP` or `reload` re-reads the city
  files without changing a running game.
- Limits on connections, logons, account creation, idle time in the Chamber, in-game frame rate and bad
  input, so one misbehaving client cannot harm another player's game (`docs/robustness.md`).
- `python -m opencs --version` and `tools/smoke.sh`, which starts a throwaway server, runs the judge's 19
  checks and stops it.
- One operator guide, [`docs/OPERATING.md`](docs/OPERATING.md), covers installing, running as a service,
  configuring, backing up and upgrading.
