import os
from pathlib import Path
from pydantic import BaseModel


class Settings(BaseModel):
    app_name: str = "AI Forge"
    host: str = "127.0.0.1"
    port: int = 3000

    # Directories
    data_dir: Path = Path.home() / ".ai-forge"
    workflows_dir: Path = Path.home() / ".ai-forge" / "workflows"
    secrets_file: Path = Path.home() / ".ai-forge" / "secrets.json"
    checkpoint_db: Path = Path.home() / ".ai-forge" / "checkpoints.db"

    # Ensure directories exist
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, **data):
        super().__init__(**data)
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings()
