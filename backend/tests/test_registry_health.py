"""Tests for the registry service skeleton (config/db/main)."""
import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_REGISTRY_DB", str(tmp_path / "registry.db"))
    monkeypatch.setenv("DAEDALUS_CAPABILITIES_REPO", str(tmp_path / "capabilities"))
    from registry.main import app
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_db_initialized_with_schema(client, tmp_path):
    db_file = tmp_path / "registry.db"
    assert db_file.exists()
    conn = sqlite3.connect(db_file)
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    finally:
        conn.close()
    assert "capability_versions" in tables
    assert "capability_fts" in tables
