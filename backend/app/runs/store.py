"""SQLite persistence layer for run metadata and event logs.

Run metadata and event logs persist in SQLite (~/.ai-forge/checkpoints.db)
alongside the graph checkpoints, so runs survive process restarts. emit() runs
on the event-loop thread while the graph executes, so writes must never block
it: they are queued and applied by a dedicated writer thread using short-lived
connections (open, write, commit, close).
"""
import json
import queue
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.sqlite_util import secure_owner_only

# Bound the in-memory store so a long-lived process doesn't grow unbounded.
MAX_RUNS = 200

_STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    status TEXT NOT NULL,
    input_data TEXT NOT NULL DEFAULT '{}',
    output_data TEXT,
    error TEXT,
    total_tokens_input INTEGER NOT NULL DEFAULT 0,
    total_tokens_output INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
    invoke_pins TEXT,
    started_at REAL NOT NULL,
    completed_at REAL
);
CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
"""

_write_queue: "queue.Queue[tuple]" = queue.Queue()
_writer_thread: threading.Thread | None = None
_writer_lock = threading.Lock()


def _store_connect(path: str) -> sqlite3.Connection:
    """A short-lived connection to the store DB (WAL, owner-only perms)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    secure_owner_only(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_STORE_SCHEMA)
    # Older DBs predate the invoke_pins column; add it in place.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    if "invoke_pins" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN invoke_pins TEXT")
        conn.commit()
    return conn


def _write_event(path: str, run_id: str, seq: int, payload: dict[str, Any]) -> None:
    conn = _store_connect(path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO events (run_id, seq, payload) VALUES (?, ?, ?)",
            (run_id, seq, json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()


def _write_summary(path: str, fields: tuple) -> None:
    conn = _store_connect(path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO runs (
                run_id, workflow_id, status, input_data, output_data, error,
                total_tokens_input, total_tokens_output, estimated_cost_usd,
                invoke_pins, started_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            fields,
        )
        conn.commit()
    finally:
        conn.close()


def _prune_store_sync(path: str) -> None:
    """Evict the oldest finished runs once the store exceeds MAX_RUNS."""
    conn = _store_connect(path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        if count <= MAX_RUNS:
            return
        rows = conn.execute(
            "SELECT run_id FROM runs WHERE status != 'running' "
            "ORDER BY started_at LIMIT ?",
            (count - MAX_RUNS,),
        ).fetchall()
        for row in rows:
            conn.execute("DELETE FROM events WHERE run_id = ?", (row["run_id"],))
            conn.execute("DELETE FROM runs WHERE run_id = ?", (row["run_id"],))
        conn.commit()
    finally:
        conn.close()


def _writer_loop() -> None:
    while True:
        item = _write_queue.get()
        try:
            if item is not None:  # None is the shutdown sentinel
                kind, path = item[0], item[1]
                if kind == "event":
                    _write_event(path, *item[2:])
                elif kind == "summary":
                    _write_summary(path, item[2])
                else:
                    _prune_store_sync(path)
        except Exception as e:  # best-effort persistence; keep the writer alive
            if item is not None:
                print(f"run store write failed ({item[0]}): {e}", file=sys.stderr)
        finally:
            _write_queue.task_done()
            if item is None:
                break


def _enqueue_write(item: tuple) -> None:
    """Queue a store write. Non-blocking and safe from the event-loop thread."""
    global _writer_thread
    _write_queue.put(item)
    with _writer_lock:
        if _writer_thread is None or not _writer_thread.is_alive():
            _writer_thread = threading.Thread(
                target=_writer_loop, name="run-store-writer", daemon=True
            )
            _writer_thread.start()


def flush_store() -> None:
    """Block until every queued write has been applied (used by tests)."""
    _write_queue.join()


def shutdown_store(timeout: float = 5.0) -> None:
    """Drain pending writes and stop the writer thread (app shutdown)."""
    global _writer_thread
    with _writer_lock:
        if _writer_thread is None or not _writer_thread.is_alive():
            return
        thread = _writer_thread
        _writer_thread = None
    _write_queue.put(None)
    thread.join(timeout)


def _persist_event(run_id: str, seq: int, payload: dict[str, Any]) -> None:
    _enqueue_write(
        ("event", str(get_settings().checkpoint_db), run_id, seq, payload)
    )


def _load_events(run_id: str) -> list[dict[str, Any]]:
    conn = _store_connect(str(get_settings().checkpoint_db))
    try:
        rows = conn.execute(
            "SELECT payload FROM events WHERE run_id = ? ORDER BY seq", (run_id,)
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(row["payload"]) for row in rows]


def _save_run_summary(record: "RunRecord") -> None:
    """Queue the record's metadata upsert so it survives a restart."""
    _enqueue_write(("summary", str(get_settings().checkpoint_db), (
        record.run_id, record.workflow_id, record.status,
        json.dumps(record.input_data), json.dumps(record.output_data),
        record.error, record.total_tokens_input,
        record.total_tokens_output, record.estimated_cost_usd,
        json.dumps(record.invoke_pins) if record.invoke_pins else None,
        record.started_at, record.completed_at,
    )))


def _load_run_summary(run_id: str) -> sqlite3.Row | None:
    conn = _store_connect(str(get_settings().checkpoint_db))
    try:
        return conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()


def _load_finished_summaries() -> list[sqlite3.Row]:
    conn = _store_connect(str(get_settings().checkpoint_db))
    try:
        return conn.execute(
            "SELECT * FROM runs WHERE status IN ('completed', 'failed')"
        ).fetchall()
    finally:
        conn.close()


def _prune_store() -> None:
    """Queue eviction of the oldest finished runs beyond MAX_RUNS."""
    _enqueue_write(("prune", str(get_settings().checkpoint_db)))


def _is_terminal(event: dict[str, Any]) -> bool:
    """True for the event that marks a run as finished (success or fatal error)."""
    if event.get("type") in ("run_end", "human_timeout"):
        return True
    if event.get("type") == "node_error" and event.get("data", {}).get("fatal"):
        return True
    return False
