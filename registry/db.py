"""Registry index — SQLite connection + schema.

Portable SQL so the engine can be swapped for Postgres later (pgvector, RLS)
without a rewrite. FTS5 gives keyword search now; vector search is pluggable
in R3.
"""
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_versions (
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    kind TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    artifact_json TEXT NOT NULL,
    stage TEXT NOT NULL,
    security_status TEXT NOT NULL,
    evaluation TEXT,
    source_commit TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (name, version)
);

CREATE INDEX IF NOT EXISTS idx_cv_name ON capability_versions(name);

-- Standalone FTS table; rowid mirrors capability_versions.rowid so the two
-- stay in lockstep inside a single transaction.
CREATE VIRTUAL TABLE IF NOT EXISTS capability_fts USING fts5(
    name, description, tags
);
"""


class Database:
    """A single aiosqlite connection for the registry's lifetime (one loop)."""

    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    @classmethod
    async def connect(cls, db_path: Path) -> "Database":
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.executescript(SCHEMA)
        await cls._migrate(conn)
        await conn.commit()
        return cls(conn)

    @staticmethod
    async def _migrate(conn: aiosqlite.Connection) -> None:
        """Add columns introduced after the initial schema to DBs created in
        place (CREATE TABLE IF NOT EXISTS never alters existing tables)."""
        rows = await conn.execute_fetchall(
            "PRAGMA table_info(capability_versions)"
        )
        columns = {r["name"] for r in rows}
        if "evaluation" not in columns:
            await conn.execute(
                "ALTER TABLE capability_versions ADD COLUMN evaluation TEXT"
            )

    async def close(self) -> None:
        await self.conn.close()
