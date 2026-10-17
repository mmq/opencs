# Architecture decision records

| # | decision |
|---|---|
| [0001](0001-stdlib-only-at-runtime.md) | Standard library only at runtime |
| [0002](0002-world-returns-events.md) | The world returns events and never performs I/O |
| [0003](0003-single-loop-no-locks.md) | One event loop; cities never await; no locks |
| [0004](0004-audiences-as-data.md) | Audiences as data, resolved by a router |
| [0005](0005-tags-at-send-boundary.md) | Tags assigned at the send boundary |
| [0006](0006-policy-centralized.md) | Policy values centralized in `policy.py` and config |
| [0007](0007-sqlite-behind-protocols.md) | SQLite for persistence, behind store protocols |
| [0008](0008-written-from-spec.md) | Written from the spec |
