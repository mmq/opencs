# 7. SQLite for persistence, behind store protocols

## Context

Accounts, progress and resume records must survive restarts, with no database server to run (DESIGN §2).

## Decision

`sqlite3` from the standard library implements the store protocols in `opencs.store`; an in-memory implementation serves tests.

## Consequences

- One file holds all state, with transactions.
- Higher layers depend on protocols, not on SQLite.
- Swapping storage later changes one package.
