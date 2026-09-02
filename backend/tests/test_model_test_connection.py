"""Tests for POST /api/models/test-connection (provider factory stubbed, no network)."""
import pytest
from fastapi.testclient import TestClient

import app.secrets as secrets_mod
import app.api.models as models_api
from app.engine.llm import LLMResult
from app.main import app


@pytest.fixture()
def secret_file(tmp_path, monkeypatch):
    """Redirect all secrets I/O to a temp file and clear env pollution."""
    path = tmp_path / "secrets.json"
    monkeypatch.setattr(secrets_mod, "_secrets_path", lambda: path)
    for name in ("TEST_MODEL_KEY", "MISSING_SECRET"):
        monkeypatch.delenv(name, raising=False)
    yield path


@pytest.fixture()
def client(secret_file):
    with TestClient(app) as c:
        yield c


class StubProvider:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.chat_calls = []

    async def chat(self, messages, tools=None, temperature=None, max_tokens=None):
        self.chat_calls.append({"messages": messages, "max_tokens": max_tokens})
        if self._error is not None:
            raise self._error
        return self._result


def _patch_provider(monkeypatch, stub=None, factory_error=None, captured=None):
    def factory(config):
        if captured is not None:
            captured.update(config)
        if factory_error is not None:
            raise factory_error
        return stub

    monkeypatch.setattr(models_api, "create_provider", factory)


def test_success(client, monkeypatch):
    stub = StubProvider(result=LLMResult(content="pong"))
    _patch_provider(monkeypatch, stub=stub)

    resp = client.post("/api/models/test-connection", json={
        "provider": "openai_compatible",
        "model": "llama3",
        "base_url": "http://localhost:11434/v1",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["model"] == "llama3"

    # The probe actually ran: one ping message, minimal max_tokens.
    assert len(stub.chat_calls) == 1
    call = stub.chat_calls[0]
    assert [m.content for m in call["messages"]] == ["ping"]
    assert call["max_tokens"] == 1


def test_secret_not_set(client, monkeypatch):
    def factory(config):
        raise AssertionError("create_provider must not be called when the secret is missing")

    monkeypatch.setattr(models_api, "create_provider", factory)

    resp = client.post("/api/models/test-connection", json={
        "model": "gpt-4o",
        "api_key_ref": "MISSING_SECRET",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "MISSING_SECRET" in body["message"]


def test_unknown_provider(client, monkeypatch):
    _patch_provider(monkeypatch, factory_error=ValueError("Unknown provider: bogus"))

    resp = client.post("/api/models/test-connection", json={
        "provider": "bogus",
        "model": "gpt-4o",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "bogus" in body["message"]


def test_anthropic_not_implemented(client, monkeypatch):
    _patch_provider(monkeypatch, factory_error=NotImplementedError("Anthropic provider coming in Phase 4"))

    resp = client.post("/api/models/test-connection", json={
        "provider": "anthropic",
        "model": "claude-3",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "not implemented" in body["message"]


def test_chat_error_does_not_leak_key(client, monkeypatch, secret_file):
    secrets_mod.set_secret("TEST_MODEL_KEY", "sk-supersecret123")
    stub = StubProvider(error=ConnectionError(
        "connection failed while authenticating with sk-supersecret123"
    ))
    captured = {}
    _patch_provider(monkeypatch, stub=stub, captured=captured)

    resp = client.post("/api/models/test-connection", json={
        "model": "gpt-4o",
        "api_key_ref": "TEST_MODEL_KEY",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["message"]
    # The resolved key value must never appear in the error message.
    assert "sk-supersecret123" not in body["message"]
    # ...but it WAS resolved and passed to the provider factory.
    assert captured.get("api_key") == "sk-supersecret123"
