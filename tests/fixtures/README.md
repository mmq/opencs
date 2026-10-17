# Test fixtures

## `spec_vectors.py`

Frames and payloads copied from I App. A.1 and I App. A.2 (real captures, byte for byte). Used by the
wire and message tests.

## `captures/*.txt`: real traffic from a test server

Each file is one connection's game traffic, made by `tools/make_fixture.py` from a capture log of a
test server (one log per run). Frames are never altered; only
whole sessions' worth of chunks are reassembled with the production `FrameReader`.

```
# source: <run>/<log file>
<t> C <hex>      client to server: the whole frame, no terminator
<t> S <hex>      server to client: the whole frame, with the terminator the server sent (0d or 0e)
<t> c <hex>      Chamber text from the client   (only with --chamber; none in the curated set)
<t> s <hex>      Chamber text from the server   (only with --chamber; none in the curated set)
```

`t` is seconds since the log's first game or Chamber line, three decimals. Older runs (July 2026)
logged only the client's bytes, so their files have no `S` lines.

Regenerate a file with `uv run python tools/make_fixture.py <log> -o <file>`; add `--chamber` to keep
the ASCII phase for M3's session tests.

### What the curated set covers

Every client message the captures ever contain appears at least once. Never captured, so covered only
by hand-built tests: A.2 `'E'` `W H L M C I D` effects and `'E'`-`'A'` Air Support, `'E'`-`'d'` Drone;
A.1 `'E'`-`'A'`, `'E'`-`'C'`, `'E'`-`'K'`, `'F'`, `'Q'` destroy, single-cell `'T'`, `'S'`, `'c'` (a
server-side relay of a shot is in none of the chosen runs). No capture holds a Muncher trip (a client
`'K'` for an id of 260 or more).

| file | what it is for |
|---|---|
| `2026-10-16-142644_002-sentinel-down-p1` | I App. A.1: Sentinel, tower, guppy legs, pickup |
| `2026-10-16-154139_004/005-grenade-relay-p1/p2` | I App. A.2: two clients, grenades, deaths, quit |
| `2026-09-03-123853_001/002-hit-dedup-p1/p2` | two-client fight (client-only logs) |
| `2026-10-13-140200_002-phase6-live-p1` | chat, echo probe, ambient, guppy legs |
| `2026-10-13-141108_002/004/005-resume-p1`, `2026-10-15-141418_003-resume-p1` | persistence and `/RESUME`; guppy legs with a late joiner |
| `2026-10-16-113841_002-play-p1`, `2026-10-16-105548_002-play-p1` | broadest message mix: deaths, modules, weather, whole-grid `'T'` |
| `2026-10-16-155148_002/004-high-ids-p1` | ids of 4096 and above |
| `2026-10-15-144318_008-new-city-p1`, `2026-10-09-003444_002-tower-mine-p1` | quit request, tower orders |
| `2026-07-29-025542_001-module-order`, `2026-07-28-203429_001-bay-upgrade` | module orders and `'Q'` subs; bay upgrades, `'D'`-`'B'` |
