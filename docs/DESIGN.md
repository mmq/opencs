# OpenCS — design

> The contract for this repository's structure.

## 1. What this is

**OpenCS** is a server compatible with the 1993 online game *CyberStrike*: one that the unmodified client can log into and play on, built from
the CyberStrike Server Specification v1.0 (the `cyberstrike-spec` repository). This is the product players use.

**Goals for 1.0**, in priority order:

1. **Correct to the spec.** Every behavior is based on the spec, and our own choices are documented as such.
2. **Readable by a newcomer, human or AI.** A reader finds any behavior by following the layers in §3,
   and each module does one thing its name says.
3. **Changeable.** Rules, policy values and wire formats live in separate places, so changing one does
   not mean touching the others.
4. **Robust on the open internet.** A malformed packet, a modified client or a dropped line never takes
   the server down or corrupts another player's game.
5. **A history that tells the story.** Built milestone by milestone, each commit a small change that
   passes the tests, ruff and mypy.

**Non-goals for 1.0:** a plugin system; a web front end; more than one server process; any change to the
client; distributing the game files (they are copyrighted and never enter the repo).

## 2. Constraints and choices

| | choice | why |
|---|---|---|
| Language | Python ≥ 3.12, `asyncio` (3.12 is what Ubuntu 24.04 LTS ships) | the spec's examples and our tools are Python; 16 players a city is a light load |
| Runtime dependencies | **none** (standard library only) | trivial to deploy anywhere; SQLite and TOML are in the standard library |
| Development tools | `pytest`, `ruff` (lint + format), `mypy --strict` on `src/`, managed with `uv` in `.venv` | type hints and a fixed style are what make the code legible to both humans and AI tools |
| Packaging | `pyproject.toml`, `src/` layout, `python -m opencs` | the standard modern layout; tests run against the installed package |
| Configuration | TOML files read with `tomllib`, validated into typed dataclasses at startup | readable, comments allowed, fails fast on a typo |
| Persistence | SQLite via `sqlite3` | one file, transactional, no server to run |
| License | MIT | owner's choice |

## 3. Architecture: layers

Each layer depends only on the layers above it in this table. **The arrows never point upward:** the wire
layer knows nothing of players, and the world knows nothing of sockets or bytes.

| layer | package | does | does not know about |
|---|---|---|---|
| **Wire** | `opencs.wire` | the three number codecs, strings, grid bytes; frame build/parse; an incremental frame reader; the Chamber's line reader and command-mode arming | sockets, messages' meaning |
| **Messages** | `opencs.messages` | one typed, immutable class per message (spec I §8), each with `encode()`/`decode()` that mirror each other; a registry from type letter (and sub-letter) to class | what a message causes |
| **Net** | `opencs.net` | the asyncio listener; one `Connection` per socket: a read pump, a write pump with a bounded outbox, the tag counter applied at the send boundary | rules, phases |
| **Sessions** | `opencs.session` | the connection's phases as explicit state machines (spec I §7.8): `Login` → `Chamber` → `InGame` → `Chamber`. Turns decoded client messages into calls on the lobby or a city | game rules |
| **Lobby** | `opencs.lobby` | who is connected and where; `/PLAY`, `/CITY`, `/WHO`…; team choice; identity and callsigns | in-game rules |
| **World** | `opencs.world` | one `City` per joinable game ("room"): pods, towers, guppies, modules, scoring, territory, weather, NPCs. **Pure rules: calls in, events out** | sockets, bytes, the clock's source |
| **Routing** | `opencs.routing` | turns an event's audience (spec I App. B) into the sessions that receive it | rules |
| **Store** | `opencs.store` | accounts, progress and `/RESUME` records behind small interfaces; a SQLite implementation and an in-memory one for tests | the game |
| **App** | `opencs.app` | reads config, builds every service, wires them, starts the listener, handles shutdown | — |

### 3.1 Package layout

```
src/opencs/
  __main__.py            python -m opencs [--config PATH]
  app.py                 wiring and shutdown
  config.py              typed config: ServerConfig, CityConfig (validated from TOML)
  policy.py              every value the server chooses (spec I §10), named, each citing its spec section
  clock.py               Clock protocol; MonotonicClock; ManualClock for tests
  wire/
    codec.py             bias10, bias88, enc_num, str, grid byte            (spec I §4.3, I §6.1)
    frame.py             build_frame, FrameReader (sans-IO, incremental)    (spec I §4.1)
    text.py              LineReader, arm()/command lines                    (spec I §5.2)
  messages/
    base.py              Message base class, registry, decode_client()
    server.py            S→C: B, A, G, H, J, I, M, N, F, P, Q, K, L, O, R, S, T, D, E-*, a, b, c, C
    client.py            C→S: S, A, b, c, I, J, U, C, D-*, E-*, M, K, Q, d
  net/
    listener.py          start_server, connection accounting and limits
    connection.py        Connection: read pump, outbox, write pump, tags
  session/
    phases.py            the phase machine (B.1)
    login.py             GEnie logon (I §5.1)
    chamber.py           lobby text session (I §5.2-§5.5, II §4)
    ingame.py            binary session: decode → city calls; quit handshake (I §5.5)
  lobby/
    lobby.py             Lobby: members, locations, city choice, teams (S9)
    commands.py          parsing /PLAY, /CITY, … (pure)
    identity.py          accounts vs JOINGENIE, callsigns (S8)
  world/
    city.py              City: owns one game's state; join/leave; tick(now)
    ids.py               id ranges and guarded reuse (I §6.2, II §8)
    view.py              ClientView: what one client has been told (late-joiner sync, I §7.3)
    events.py            Event = (message, Audience)
    pods.py              a player's pod life: drop, death handshake, respawn (I §7.1, I §7.5, II §7, I §7.8)
    combat.py            shot log, attribution, hit set, damage byte (I §9.1, II §9.3)
    death.py             a death: payout, 'E'-'K' notices, 'J', bounty zero (I §9.2.9, II §11.2)
    player.py            Player: identity, slot and every per-player record (I §6.3)
    grace.py             the spawn-grace mirror of the client's rule (I §9.1.5)
    scoring.py           bounty, multiplier floor, status messages (I §9.2, I §9.2.5)
    territory.py         block classes (I §8.22, S4)
    weather.py           a city's ambient program (I §8.20)
    logistics.py         guppies and orders (I §9.4, I §9.4.3)
    orders.py            tower orders, guppy actions to events, towers placed and destroyed (I §9.4, S3)
    modules.py           catalog, drops, pickup, eject (I §9.3.1, I §9.3.3)
    effects.py           effect-module requests (I §9.3.5, II §16.1)
    structures.py        towers and their durability (S3)
    npcs/sentinels.py    resident Sentinels (S6)
    npcs/munchers.py     Munchers (I §9.5.2)
  routing.py             Audience → sessions
  store/
    base.py              AccountStore, ProgressStore, ResumeStore protocols
    sqlite.py            SQLite implementation, schema, migrations
    memory.py            in-memory implementation (tests)
config/
  server.toml            port, data directory, limits, logging
  cities/A.toml B.toml C.toml
tests/
  unit/                  one test module per source module
  replay/                real captures as fixtures, replayed through the world
  e2e/                   the black-box judge against a running server
tools/
  fake_client.py         the scripted client (built on `opencs.wire`)
  judge.py               the 19-check black-box test
```

### 3.2 How a message flows

```
socket bytes ─► Connection.read_pump ─► FrameReader ─► Frame ─► messages.decode_client() ─► ClientMessage
     ─► InGameSession.handle(msg) ─► City.method(player, …)  ─► list[Event]
     ─► Router.deliver(events) ─► each target Connection.outbox ─► write_pump: assign tag, encode, write
```

- **The world never sends and never awaits.** A `City` method takes a typed request and returns
  `list[Event]`; each `Event` is a server message plus an `Audience`. That keeps every rule testable with
  no sockets, and lets the replay tests drive real captured traffic through it.
- **Audiences are data** (`ToPlayer(id)`, `ToAll`, `ToAllExcept(id)`, `ToTeam(team)`,
  `ToHolders(entity_id)`), resolved by the router — the spec's A.4 table becomes one small module.
- **Tags are assigned at the send boundary** (spec I §4.2), never by the code that builds a message.

### 3.3 Concurrency

- One asyncio event loop. **Each `City` is changed only from the event loop, and its methods never
  `await`**, so no city state is ever seen half-updated and no locks are needed. This is the "room" model
  common to multiplayer servers.
- Each city has one ticker task calling `City.tick(now)` at a fixed rate (`policy.TICK_HZ`) for timed
  rules: bounty accrual, the drop release, guppy queues, NPC timers, weather.
- Each connection has a read task and a write task. A slow client fills only its own bounded outbox; when
  that overflows, that client is disconnected, never the others slowed.

### 3.4 Time

All rules read time from a `Clock` passed in, never from `time` directly. Tests use `ManualClock` and
advance it; nothing in the test suite sleeps.

### 3.5 State per client inside the world

Some rules depend on what one client has been told: which entities it holds (late joiners, I §7.3), which
pods it has been introduced to (I §7.2), a guppy's or Muncher's leg count as that client sees it. That lives
in `world/view.py` as one `ClientView` per player, owned by the city. Sessions do not keep game state.

## 4. Policy, configuration and the spec

- **Every value the server chooses** (spec I §10) is a named constant in
  `policy.py` or a field in the config, with a comment naming its spec section. No unexplained number
  appears anywhere else.
- **Cities are configuration** (`config/cities/*.toml`): letter, name, map, multiplier cap, tower output,
  module level, banned modules, block-class map, weather program, NPC population, team policy.
- **The spec lives in its own repository** (`cyberstrike-spec`, spec v1.0). Code cites it as `I §n`, `II §n`,
  `III §n` or `IV §n` (part and section). **Where the spec does not cover a behavior, the choice is the server's own** and
  is documented where it is made (see the first bullet).

## 5. Robustness and security

- **Input is hostile.** A frame that fails its checksum or length is dropped and counted; a message that
  fails to decode is dropped and counted; a connection that passes `policy.MAX_BAD_FRAMES` is closed.
  **No input can raise out of a session**: the session's handler catches, logs with context and carries on
  or closes that one connection.
- **Limits:** connections in total and per address, line length in the Chamber (the client's armed buffer
  is 512 bytes, spec I §5.2), outbox size, login attempts.
- **Never send what crashes the client** (spec II §8): message constructors validate their fields
  (`'c'` KIND, `'R'` ranges, `'T'` classes, chat bytes) and raise in our code rather than on a player's
  screen.
- **Passwords:** salted PBKDF2 from `hashlib`, never stored or logged in clear.
- **Bugs vs bad input:** an exception inside a `City` is a bug — logged with the triggering request, the
  request dropped, the city kept running.

## 6. Observability and operations

- `logging` with one logger per layer and a per-connection adapter (connection id, callsign).
- **Optional wire capture** per connection, in the replay format the tests read, so a bug a player hits
  becomes a test fixture.
- An admin channel (M12): status, announce, kick, graceful shutdown that saves every player's progress.
- Graceful shutdown on SIGINT/SIGTERM: stop accepting, save progress, close connections.

## 7. Testing

| level | what | where |
|---|---|---|
| unit | codecs and frames against the spec's hex (A.3, A.3b); every message round-trips; every rule module with a `ManualClock` | `tests/unit/` |
| replay | real captured client traffic (deaths, guppy legs, Muncher trips, rest reports) driven through the world, checked against what the test server did | `tests/replay/` |
| end-to-end | the judge: two scripted clients against a running server, 19 checks | `tests/e2e/`, `tools/judge.py` |
| live | two real clients, only for what must be seen | a round at the end of each milestone |

Rules: **every commit passes `pytest`, `ruff` and `mypy`**; a bug fix comes with a test shown to fail
without it; a new rule comes with its test in the same commit.

## 8. Conventions

- Type hints everywhere; `mypy --strict` clean. Dataclasses (`frozen=True, slots=True`) for messages,
  events and config.
- One concept per module; a module over ~400 lines is a prompt to split.
- Docstrings say *what and why* and cite the spec as `I §n`, `II §n` or `III §n`; they do not narrate history (git does that).
- No binary addresses in the code (the spec carries provenance).
- Names follow the spec's vocabulary: pod, tower, guppy, Muncher, Sentinel, bay, bounty, multiplier,
  territory, city, map.

## 9. Decisions to record as ADRs (`docs/decisions/`)

1. Standard library only at runtime.
2. The world returns events and never performs I/O.
3. One event loop; cities are never awaited inside; no locks.
4. Audiences as data, resolved by a router.
5. Tags assigned at the send boundary.
6. Policy values centralized in `policy.py` and config, each citing the spec.
7. SQLite for persistence, behind store protocols.
8. Written from the spec.

## 10. Naming and trademarks

The project is **OpenCS**, package `opencs`. *CyberStrike* is a trademark of Simutronics Corp.; the name
appears only as a factual statement of compatibility ("a server compatible with CyberStrike"), never as the
project's name, in logos or in branding. The repository never contains the game's files: its
program, maps or manual.
