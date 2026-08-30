"""Pytest bootstrap: make `schema` and `app` importable from any invocation dir.

Mirrors the sys.path setup in backend/cli.py so tests can import both the
top-level `schema` package (repo root) and the `app` package (backend/).
"""
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent
for _p in (_PROJECT_ROOT, _PROJECT_ROOT / "backend"):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)


@pytest.fixture(autouse=True)
def _isolated_checkpoint_db(tmp_path, monkeypatch):
    """Give every test its own checkpoint SQLite file (no cross-test bleed)."""
    monkeypatch.setenv("AI_FORGE_CHECKPOINT_DB", str(tmp_path / "checkpoints.db"))
