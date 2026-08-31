import os
import stat

from app.sqlite_util import secure_owner_only


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_secure_owner_only_chmods_db_and_sidecars(tmp_path):
    db = tmp_path / "checkpoints.db"
    db.write_bytes(b"")
    (db.parent / f"{db.name}-wal").write_bytes(b"")
    (db.parent / f"{db.name}-shm").write_bytes(b"")
    for p in (db, db.parent / f"{db.name}-wal", db.parent / f"{db.name}-shm"):
        os.chmod(p, 0o644)

    secure_owner_only(str(db))

    assert _mode(db) == 0o600
    assert _mode(db.parent / f"{db.name}-wal") == 0o600
    assert _mode(db.parent / f"{db.name}-shm") == 0o600


def test_secure_owner_only_noop_when_missing(tmp_path):
    secure_owner_only(str(tmp_path / "nonexistent.db"))
