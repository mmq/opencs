# 9. Rate limits belong to the connection, not the phase

## Context

A connection moves between phases as often as its client likes: `/QUIT` then `M 1380;3` re-enters the Chamber, and `/PLAY` starts a new game. Each phase used to build its own token buckets and bad-input counters, so every switch gave a flooder a full allowance, and a loop of `/QUIT` and `M 1380;3` made Chamber chat effectively unlimited (DESIGN §5).

## Decision

Every bucket and windowed counter that limits a client lives in one `session.budgets.Budgets`, made once in `Session.__init__`. Phases spend from `host.budgets` and never build their own. The trip from the Chamber to the `*` prompt also costs a token (`CHAMBER_REENTRY_LIMIT`), which bounds the work of re-entering.

## Consequences

- A phase switch neither grants nor costs an allowance; a bucket refills only with time.
- A new limit on client input goes in `Budgets`, unless it spans connections. Those (per address, per account, server-wide) live in shared services such as `LoginLimits`.
- Per-phase state that is not a limit (a warning already given, a parser's buffer) stays in the phase.
