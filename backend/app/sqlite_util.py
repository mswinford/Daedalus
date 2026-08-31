"""Shared SQLite helpers."""
import os


def secure_owner_only(db_path: str) -> None:
    """Keep the DB file (and its WAL/SHM sidecars) owner-only, like secrets.json."""
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(db_path + suffix):
            os.chmod(db_path + suffix, 0o600)
