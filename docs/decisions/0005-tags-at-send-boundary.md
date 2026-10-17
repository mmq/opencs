# 5. Tags assigned at the send boundary

## Context

Every frame carries a tag from a per-connection counter (spec I §4.2, II §7). If message builders assigned tags, ordering across code paths could produce gaps or repeats.

## Decision

The write pump assigns the tag as it encodes each message for the wire. Message classes and rule code never see tags.

## Consequences

- Tag order always matches send order.
- Messages are pure values, equal when their content is equal.
- Tests of rules need no tag bookkeeping.
