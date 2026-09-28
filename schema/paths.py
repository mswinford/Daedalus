"""Shared filesystem paths for Daedalus user data."""
from pathlib import Path


def data_dir() -> Path:
    """Top-level user data directory (~/.daedalus).

    One-time migration: if the legacy ~/.ai-forge exists and ~/.daedalus does
    not, rename it in place (atomic on the same filesystem). If both exist,
    the new one wins and the legacy dir is left untouched.
    """
    d = Path.home() / ".daedalus"
    old = Path.home() / ".ai-forge"
    if not d.exists() and old.exists():
        old.rename(d)
    return d
