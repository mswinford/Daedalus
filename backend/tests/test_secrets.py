"""Tests for secrets store: core module + API endpoints."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.secrets as secrets_mod
from app.main import app


@pytest.fixture()
def secret_file(tmp_path, monkeypatch):
    """Redirect all secrets I/O to a temp file and clear env pollution."""
    path = tmp_path / "secrets.json"
    monkeypatch.setattr(secrets_mod, "_secrets_path", lambda: path)
    # Remove any real env vars we'll use in tests
    for name in ("TEST_SECRET_A", "TEST_SECRET_B", "ENV_ONLY"):
        monkeypatch.delenv(name, raising=False)
    yield path


# ── Core module tests ───────────────────────────────────────────────

def test_load_secrets_empty_when_file_missing(secret_file):
    assert secrets_mod.load_secrets() == {}


def test_set_and_get_secret(secret_file):
    secrets_mod.set_secret("TEST_SECRET_A", "value-a")
    assert secrets_mod.get_secret("TEST_SECRET_A") == "value-a"


def test_get_secret_env_precedence(secret_file, monkeypatch):
    secrets_mod.set_secret("TEST_SECRET_A", "file-value")
    monkeypatch.setenv("TEST_SECRET_A", "env-value")
    assert secrets_mod.get_secret("TEST_SECRET_A") == "env-value"


def test_get_secret_missing_returns_none(secret_file):
    assert secrets_mod.get_secret("NONEXISTENT") is None


def test_set_secret_upsert(secret_file):
    secrets_mod.set_secret("TEST_SECRET_A", "first")
    secrets_mod.set_secret("TEST_SECRET_A", "second")
    assert secrets_mod.get_secret("TEST_SECRET_A") == "second"


def test_delete_secret(secret_file):
    secrets_mod.set_secret("TEST_SECRET_A", "x")
    assert secrets_mod.delete_secret("TEST_SECRET_A") is True
    assert secrets_mod.get_secret("TEST_SECRET_A") is None


def test_delete_secret_not_found(secret_file):
    assert secrets_mod.delete_secret("NOPE") is False


def test_list_secrets_no_values(secret_file):
    secrets_mod.set_secret("ALPHA", "1")
    secrets_mod.set_secret("BETA", "2")
    result = secrets_mod.list_secrets()
    names = [r["name"] for r in result]
    assert names == ["ALPHA", "BETA"]
    # No value field should be present
    for entry in result:
        assert "value" not in entry


def test_list_secrets_env_source(secret_file, monkeypatch):
    secrets_mod.set_secret("TEST_SECRET_A", "file-val")
    monkeypatch.setenv("TEST_SECRET_A", "env-val")
    result = secrets_mod.list_secrets()
    entry = next(r for r in result if r["name"] == "TEST_SECRET_A")
    assert entry["source"] == "env"


def test_set_secret_creates_parent_dirs(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "dir" / "secrets.json"
    monkeypatch.setattr(secrets_mod, "_secrets_path", lambda: path)
    secrets_mod.set_secret("X", "y")
    assert path.exists()


# ── API endpoint tests ──────────────────────────────────────────────

@pytest.fixture()
def client(secret_file):
    with TestClient(app) as c:
        yield c


def test_api_list_empty(client):
    resp = client.get("/api/secrets")
    assert resp.status_code == 200
    assert resp.json() == []


def test_api_upsert_and_list(client, secret_file):
    resp = client.put("/api/secrets", json={"name": "MY_KEY", "value": "s3cret"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "name": "MY_KEY"}

    resp = client.get("/api/secrets")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "MY_KEY"
    assert data[0]["source"] == "file"
    assert data[0]["set"] is True


def test_api_upsert_empty_name_400(client):
    resp = client.put("/api/secrets", json={"name": "  ", "value": "x"})
    assert resp.status_code == 400


def test_api_delete(client, secret_file):
    client.put("/api/secrets", json={"name": "TO_DELETE", "value": "v"})
    resp = client.delete("/api/secrets/TO_DELETE")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Confirm gone
    resp = client.get("/api/secrets")
    assert resp.json() == []


def test_api_delete_not_found_404(client):
    resp = client.delete("/api/secrets/NEVER_EXISTED")
    assert resp.status_code == 404
