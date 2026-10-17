"""The SQLite store: accounts and progress in one file (DESIGN §2, ADR 7).

One connection, used only from the event loop thread (``check_same_thread`` stays on, so a stray
thread fails loudly). A save is a single small row in its own transaction, which costs one
journal sync (a few milliseconds on a disk); a server that grew to feel that would move saves to a
thread, which the protocols allow but nothing needs yet.

The schema is versioned in a ``schema_version`` table and only ever moves forward: ``MIGRATIONS``
is an append-only list, the n-th entry taking a database from version n-1 to n. A database from a
newer server is refused rather than guessed at.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from opencs import policy
from opencs.store.base import Account, ProgressRecord, Stores

#: Append-only. Never edit an entry that has shipped: add a new one.
MIGRATIONS: tuple[tuple[str, ...], ...] = (
    (
        """CREATE TABLE accounts (
            name TEXT PRIMARY KEY,
            salt BLOB NOT NULL,
            password_hash BLOB NOT NULL,
            iterations INTEGER NOT NULL,
            created REAL NOT NULL
        )""",
        """CREATE TABLE progress (
            identity TEXT PRIMARY KEY,
            bays TEXT NOT NULL,
            marker INTEGER NOT NULL,
            bay_points INTEGER NOT NULL,
            score INTEGER NOT NULL,
            career INTEGER NOT NULL,
            updated REAL NOT NULL
        )""",
    ),
)


class SchemaError(Exception):
    """The database cannot be used by this server."""


class SqliteStore:
    """``AccountStore`` and ``ProgressStore`` over one SQLite file."""

    def __init__(
        self,
        path: Path | str,
        *,
        now: Callable[[], float] = time.time,
        migrations: Sequence[Sequence[str]] = MIGRATIONS,
    ) -> None:
        self._now = now
        # Autocommit mode, so every transaction below is an explicit BEGIN .. COMMIT.
        self._db = sqlite3.connect(path, isolation_level=None)
        try:
            self._migrate(migrations)
        except BaseException:
            self._db.close()
            raise

    # --- lifecycle ---------------------------------------------------------------------------

    def close(self) -> None:
        self._db.close()

    @property
    def schema_version(self) -> int:
        row = self._db.execute("SELECT version FROM schema_version").fetchone()
        return int(row[0])

    def _migrate(self, migrations: Sequence[Sequence[str]]) -> None:
        self._db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = self._db.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            self._db.execute("INSERT INTO schema_version VALUES (0)")
            current = 0
        else:
            current = int(row[0])
        if current > len(migrations):
            raise SchemaError(
                f"the database is at schema version {current}, newer than this server's "
                f"{len(migrations)}"
            )
        for version in range(current + 1, len(migrations) + 1):
            self._db.execute("BEGIN")
            try:
                for statement in migrations[version - 1]:
                    self._db.execute(statement)
                self._db.execute("UPDATE schema_version SET version = ?", (version,))
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def _write(self, sql: str, params: tuple[object, ...]) -> sqlite3.Cursor:
        """One statement in its own transaction."""
        self._db.execute("BEGIN")
        try:
            cursor = self._db.execute(sql, params)
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")
        return cursor

    # --- AccountStore ------------------------------------------------------------------------

    def get(self, name: str) -> Account | None:
        row = self._db.execute(
            "SELECT name, salt, password_hash, iterations FROM accounts WHERE name = ?", (name,)
        ).fetchone()
        return None if row is None else Account(row[0], bytes(row[1]), bytes(row[2]), row[3])

    def create(self, account: Account) -> bool:
        cursor = self._write(
            "INSERT OR IGNORE INTO accounts VALUES (?, ?, ?, ?, ?)",
            (
                account.name,
                account.salt,
                account.password_hash,
                account.iterations,
                self._now(),
            ),
        )
        return cursor.rowcount == 1

    def callsign_registered(self, callsign: str, length: int) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM accounts WHERE substr(name, 1, ?) = ? LIMIT 1", (length, callsign)
        ).fetchone()
        return row is not None

    # --- ProgressStore -----------------------------------------------------------------------

    def load(self, identity: str) -> ProgressRecord | None:
        row = self._db.execute(
            "SELECT bays, marker, bay_points, score, career, updated FROM progress "
            "WHERE identity = ?",
            (identity,),
        ).fetchone()
        if row is None:
            return None
        bays = tuple(int(n) for n in row[0].split(","))
        return ProgressRecord(bays, row[1], row[2], row[3], row[4], row[5])

    def save(self, identity: str, record: ProgressRecord) -> None:
        self._write(
            "INSERT INTO progress (identity, bays, marker, bay_points, score, career, updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(identity) DO UPDATE SET "
            "bays = excluded.bays, marker = excluded.marker, bay_points = excluded.bay_points, "
            "score = excluded.score, career = excluded.career, updated = excluded.updated",
            (
                identity,
                ",".join(str(n) for n in record.bays),
                record.marker,
                record.bay_points,
                record.score,
                record.career,
                self._now(),
            ),
        )


def open_stores(
    data_dir: Path, *, now: Callable[[], float] = time.time
) -> tuple[Stores, SqliteStore]:
    """Create ``data_dir`` if needed and open its database. Returns the stores and the one object
    to ``close`` when the server stops."""
    data_dir.mkdir(parents=True, exist_ok=True)
    db = SqliteStore(data_dir / policy.DATABASE_FILE_NAME, now=now)
    return Stores(db, db), db
