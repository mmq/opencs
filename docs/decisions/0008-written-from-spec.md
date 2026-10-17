# 8. Written from the spec

## Context

OpenCS needs one source of truth for what a server must do. Code taken from another server would carry that server's accidents and blur which behavior the spec actually demands.

## Decision

OpenCS is written from spec v1.0 alone. Captures of test servers are evidence for tests, never a source of code.

## Consequences

- Behavior traces to a spec section, or to a choice recorded in `policy.py`.
- Some work is done that copying would have saved; the code is the better for it.
- The game's own files never enter the repository.
