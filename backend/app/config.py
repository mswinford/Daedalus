import os
from pathlib import Path
from pydantic import BaseModel

from schema.paths import data_dir as _data_dir

_DATA = _data_dir()


class Settings(BaseModel):
    app_name: str = "Daedalus"
    host: str = "127.0.0.1"
    port: int = 3000

    # Directories
    data_dir: Path = _DATA
    workflows_dir: Path = _DATA / "workflows"
    secrets_file: Path = _DATA / "secrets.json"
    checkpoint_db: Path = _DATA / "checkpoints.db"

    # Ensure directories exist
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, **data):
        # Overridable (e.g. tests) without touching the rest of the defaults.
        if "checkpoint_db" not in data:
            env = os.environ.get("DAEDALUS_CHECKPOINT_DB")
            if env:
                data["checkpoint_db"] = env
        super().__init__(**data)
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings()
