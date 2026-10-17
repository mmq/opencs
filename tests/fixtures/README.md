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

Regenerate a file with `uv run python tools/make_fixture.py <log> -o <file>`; add `--info-frames` for a log without `game tx` lines (the server frames are then lifted from the INFO lines); add `--chamber`
to keep the ASCII phase for M3's session tests.

### What the curated set covers

Every client message the captures ever contain appears at least once. Never captured, so covered only
by hand-built tests: A.2 `'E'` `W H L M C I D` effects and `'E'`-`'A'` Air Support, `'E'`-`'d'` Drone;
A.1 `'E'`-`'A'`, `'E'`-`'C'`, `'E'`-`'K'`, `'F'`, `'Q'` destroy, single-cell `'T'`, `'S'`, `'c'` (a
server-side relay of a shot is in none of the chosen runs). No capture holds a Muncher trip (a client
`'K'` for an id of 260 or more), and no log in the capture corpus mentions a Muncher,
so `tests/replay/test_muncher_replay.py` drives a modeled client instead (no captured Muncher trip exists).

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
| `2026-07-29-030741_002-city-name` | July build: client `'b'` frames with a trailing NUL inside LEN (I §4.1) |

### Fixtures added for M6 (deaths and attribution)

Every capture with a server-annotated `DEATH of` and a size under about 100 KB of fixture, plus the
contested attribution run, so the death and attribution replays run on real streams:
`2026-09-15-101546_001/002` and `2026-09-15-105814_001` (bounty payouts), `2026-09-15-102453_001` (the
contested attribution run: 15 own shots, 46 Sentinel shots), `2026-09-03-104918_001/002` (phalanx),
`2026-09-02-000110_001` (8 deaths), `2026-07-29-*`, `2026-07-30-*` and `2026-08-30-152217_*` (two-player
deaths). The runs `2026-07-30-213906_001/002` are left out: they hold the short client `'b'` frames that do
not decode (I §4.1).

### Fixtures added for M8 (the loadout ledger replay)

| file | what it is for |
|---|---|
| `2026-07-29-015155_001-module-rack` | three `'Q'` sub 5 installs the client never took; made with `--info-frames`, because the log has no `game tx` lines and carries the server frames as hex on INFO lines |
| `2026-10-13-141108_007-resume-p1` | module installs and server traffic in a `/RESUME` run |
| `2026-10-15-141418_005-resume-p1`, `2026-10-15-141418_007-resume-p1` | the same, two more resume runs |
| `2026-10-15-175552_002-new-city-p1` | module installs in a new-city run |

### Runs that are not in the corpus

The kill and payout runs `2026-10-01-170222` and `2026-10-01-172859` are not in the capture corpus, and no other run of 2026-10-01 stands in. Kills (server `'J'`) are carried by
`2026-10-16-113841_002-play-p1`, `2026-10-16-142644_002-sentinel-down-p1` and the two
`2026-10-16-154139` grenade-relay fixtures. Payouts (`'D'`-`'S'`) appear in `2026-10-16-113841_002-play-p1`
(3 frames) and in the resume and high-id fixtures; M6-M7 should replay those.

The "never captured" list above was checked against the whole corpus (363 logs), not only the chosen runs.
