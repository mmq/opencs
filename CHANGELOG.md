# Changelog

OpenCS is built one milestone at a time; each milestone is one `--no-ff` merge on
`main`, so `git log --first-parent main` reads as the chapter list. Newest first.

## 1.0.0rc1 (release candidate)

### M13: release
- Player guide (`docs/PLAYING.md`) and deployment guide (`docs/DEPLOYMENT.md`); README final pass.
- `python -m opencs --version`; package metadata; this changelog.
- Tests that the repository holds no game file and is never named after the game.
- `tools/smoke.sh`: start a server, run the judge's 19 checks, stop it.
- Fix: the systemd unit in `docs/operations.md` had trailing comments that systemd reads as part of
  the directive values.

### M12: operations (tag `m12`)
Logging configured from `server.toml` (a logger per layer, an optional rotating file, passwords never
logged); optional per-connection wire capture in the replay-fixture format; a token-guarded admin
channel on 127.0.0.1 (status, announce, kick, shutdown, reload, a stranded-guppy failsafe); graceful
shutdown on SIGINT/SIGTERM that saves everyone within a deadline; city reload that never changes a
running game. `docs/operations.md`.

### M11: hardening (tag `m11`)
Every input path audited (`docs/robustness.md`): connection, login and account-creation limits, a
Chamber idle timeout, an in-game frame rate limit, windowed bad-input accounting, containment of any
exception to its own session, outbox overflow isolation. Fuzz suites for the net and session layers and
a simulated 48-hour city; the pod id space is used whole and released ids are reused safely.

### M10b: /RESUME (tag `m10b`)
A line drop from a real account holds the game for 10 minutes, with 3 minutes to type `/RESUME` once
back in the Chamber. A drop within 20 seconds of an attack counts as a death. Bays, marker, score,
multiplier and modules are restored.

### M10a: persistence (tag `m10a`)
Accounts and progress in SQLite behind store protocols (forward-only migrations), saved under the
spec's guards and restored at session start; `/SCORE` and `/WHO <callsign>` show the record.

### M9: weather, Sentinels, Munchers (tag `m9`)
Per-city weather programs, resident Sentinels that are worn down and replaced, called and free
Munchers; a correction to how a guppy's or Muncher's legs are acted on when clients report at
different times.

### M8: modules and effects (tag `m8`)
Module purchase orders and guppy deliveries, dropped modules (pickup, eject, used-up), the
installed-module ledger, and every effect the server carries out (Repair, Harden, Immunize, Liberator,
Weather, Mine, Mine Sweep, Air Support).

### M7: towers, guppies, territory (tag `m7`)
Tower orders flown in by the team's guppy, the late-joiner rule, tower durability, and per-city
territory block maps.

### M6: combat and scoring (tag `m6`)
Shots and hits between players, hit attribution, damage relay, bounty accrual and payout, the death
settlement, entry and respawn floors.

### M5: two pods (tag `m5`)
Players in one city see each other: introductions, the pod state relay, open and team chat, the
Grenade relay, late joiners, and the audience router.

### M4: one pod (tag `m4`)
The world's first rules (calls in, events out): joining a city, the pod's life, the latency ping, and
the quit handshake that returns the player to the Chamber on the same connection.

### M3: net, login, Chamber and lobby (tag `m3`)
The asyncio listener and connection, the logon, the Chamber and every lobby command, guest callsigns
and password accounts, city and team choice, and the launch sequence. The fake client and the judge.

### M2: messages (tag `m2`)
One typed class per protocol message (34 server, 24 client) on a registry, constructor validation of
the fields that crash the client, and curated replay fixtures of protocol bytes.

### M1: the wire layer (tag `m1`)
The primitive codecs, framing with resynchronization, and the Chamber text helpers.

### M0: skeleton (tag `m0`)
The project: layout, MIT license, standard-library-only runtime, lint and type configuration, clocks,
policy, typed TOML config with the three shipped cities, and a server that starts and stops cleanly.
