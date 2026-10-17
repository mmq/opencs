# 1. Standard library only at runtime

## Context

The server must be trivial to deploy anywhere. Python ships asyncio, sqlite3, tomllib and hashlib, which cover transport, persistence, configuration and password hashing (DESIGN §2).

## Decision

`pyproject.toml` declares no runtime dependencies. Development tools (pytest, ruff, mypy) live in the uv dev dependency group only.

## Consequences

- Deployment is a Python 3.12 interpreter and the source tree.
- We write small utilities ourselves instead of importing them.
- Adding a runtime dependency needs a new ADR superseding this one.
