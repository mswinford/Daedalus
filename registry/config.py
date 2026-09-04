import os
from pathlib import Path
from pydantic import BaseModel

from schema.paths import data_dir as _data_dir

_DATA = _data_dir()


class Settings(BaseModel):
    app_name: str = "Daedalus Registry"
    host: str = "127.0.0.1"
    port: int = 3010

    # Directories / files (env-overridable, mirroring backend/app/config.py)
    data_dir: Path = _DATA
    registry_db: Path = _DATA / "registry.db"
    capabilities_repo: Path = _DATA / "capabilities"

    # Optional git remote for the capabilities repo (empty = local-only).
    # When set, publishes push here and startup fetches+rebases from it.
    capabilities_remote: str = ""
    # Optional token injected into HTTPS remotes in-memory only (never
    # persisted to .git/config). SSH / host credential helpers work without it.
    git_token: str = ""

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, **data):
        if "registry_db" not in data:
            env = os.environ.get("DAEDALUS_REGISTRY_DB")
            if env:
                data["registry_db"] = Path(env)
        if "capabilities_repo" not in data:
            env = os.environ.get("DAEDALUS_CAPABILITIES_REPO")
            if env:
                data["capabilities_repo"] = Path(env)
        if "capabilities_remote" not in data:
            env = os.environ.get("DAEDALUS_CAPABILITIES_REMOTE")
            if env:
                data["capabilities_remote"] = env
        if "git_token" not in data:
            env = os.environ.get("DAEDALUS_GIT_TOKEN")
            if env:
                data["git_token"] = env
        super().__init__(**data)


def get_settings() -> Settings:
    return Settings()
