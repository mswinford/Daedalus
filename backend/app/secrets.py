"""Secrets storage: ~/.daedalus/secrets.json with env-var precedence.

Resolution order for a given name:
  1. Process environment variable (os.environ)
  2. secrets.json file on disk

The file is a flat JSON object: {"GITHUB_TOKEN": "ghp_...", ...}
"""
import json
import os
from pathlib import Path

from app.config import get_settings


def _secrets_path() -> Path:
    return get_settings().secrets_file


def load_secrets() -> dict[str, str]:
    """Read all secrets from the file. Returns {} if the file doesn't exist."""
    path = _secrets_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def get_secret(name: str) -> str | None:
    """Resolve a secret by name: env var first, then file. Returns None if unset."""
    val = os.environ.get(name)
    if val is not None:
        return val
    secrets = load_secrets()
    return secrets.get(name)


def set_secret(name: str, value: str) -> None:
    """Upsert a secret in the file (does not touch env vars)."""
    path = _secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    secrets = load_secrets()
    secrets[name] = value
    path.write_text(json.dumps(secrets, indent=2))
    os.chmod(path, 0o600)


def delete_secret(name: str) -> bool:
    """Remove a secret from the file. Returns True if it was present."""
    path = _secrets_path()
    if not path.exists():
        return False
    secrets = load_secrets()
    if name not in secrets:
        return False
    del secrets[name]
    path.write_text(json.dumps(secrets, indent=2))
    os.chmod(path, 0o600)
    return True


def list_secrets() -> list[dict[str, str | bool]]:
    """List all known secret names with their source. Never returns values."""
    file_secrets = load_secrets()
    seen: dict[str, dict[str, str | bool]] = {}

    for name in file_secrets:
        seen[name] = {"name": name, "source": "file", "set": True}

    for name in file_secrets:
        if name in os.environ:
            seen[name]["source"] = "env"

    return sorted(seen.values(), key=lambda x: x["name"])
