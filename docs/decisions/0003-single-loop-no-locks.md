# 3. One event loop; cities never await; no locks

## Context

Players of one city interact constantly. Shared mutable state with threads or awaiting inside rules would need locks and invite half-updated states (DESIGN §3.3).

## Decision

One asyncio loop runs everything. A city is changed only from the loop and its methods never `await`, so each call is atomic. Each city has one ticker task calling `tick(now)` at `policy.TICK_HZ`.

## Consequences

- No locks anywhere in the game state.
- Scale is one process; 16 players a city is a light load.
- A slow city rule blocks the loop, so rules must stay cheap.
