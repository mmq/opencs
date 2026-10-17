# 2. The world returns events and never performs I/O

## Context

Game rules must be testable without sockets, and captured traffic must be replayable through them (DESIGN §3.2).

## Decision

A `City` method takes a typed request and returns `list[Event]`, each a server message plus an `Audience`. The world never sends, never reads the clock's source, and never awaits.

## Consequences

- Every rule is a plain function call in a unit test.
- Replay tests drive real captures through the world.
- The session and routing layers carry all delivery work.
