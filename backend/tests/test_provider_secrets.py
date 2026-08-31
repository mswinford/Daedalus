"""Tests for api_key_ref secret resolution during provider construction."""
import pytest

import app.secrets as secrets_mod
from app.engine.builder import GraphBuilder
from schema.models import Workflow, ModelConfig


@pytest.fixture()
def secret_file(tmp_path, monkeypatch):
    """Redirect all secrets I/O to a temp file and clear env pollution."""
    path = tmp_path / "secrets.json"
    monkeypatch.setattr(secrets_mod, "_secrets_path", lambda: path)
    for name in ("TEST_MODEL_KEY"):
        monkeypatch.delenv(name, raising=False)
    yield path


def _wf(api_key_ref):
    return Workflow(
        id="wf", name="wf",
        models=[ModelConfig(
            id="m1", name="M", provider="openai_compatible", model="x",
            api_key_ref=api_key_ref,
        )],
    )


def _provider(wf):
    return GraphBuilder(wf).providers["m1"]


def test_existing_secret_resolves_to_value(secret_file):
    secrets_mod.set_secret("TEST_MODEL_KEY", "sk-secret-value")
    provider = _provider(_wf("TEST_MODEL_KEY"))
    assert provider.api_key == "sk-secret-value"


def test_env_var_takes_precedence_over_file(secret_file, monkeypatch):
    secrets_mod.set_secret("TEST_MODEL_KEY", "file-value")
    monkeypatch.setenv("TEST_MODEL_KEY", "env-value")
    provider = _provider(_wf("TEST_MODEL_KEY"))
    assert provider.api_key == "env-value"


def test_unset_api_key_ref_uses_fallback(secret_file):
    provider = _provider(_wf(None))
    assert provider.api_key == "not-needed"


def test_empty_api_key_ref_uses_fallback(secret_file):
    provider = _provider(_wf(""))
    assert provider.api_key == "not-needed"


def test_missing_secret_falls_back_to_raw_string(secret_file):
    provider = _provider(_wf("sk-pasted-raw-key"))
    assert provider.api_key == "sk-pasted-raw-key"
