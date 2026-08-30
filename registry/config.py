import os
from pathlib import Path
from pydantic import BaseModel


class Settings(BaseModel):
    app_name: str = "AI Forge Registry"
    host: str = "127.0.0.1"
    port: int = 3010

    # Directories / files (env-overridable, mirroring backend/app/config.py)
    data_dir: Path = Path.home() / ".ai-forge"
    registry_db: Path = Path.home() / ".ai-forge" / "registry.db"
    capabilities_repo: Path = Path.home() / ".ai-forge" / "capabilities"

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, **data):
        if "registry_db" not in data:
            env = os.environ.get("AI_FORGE_REGISTRY_DB")
            if env:
                data["registry_db"] = Path(env)
        if "capabilities_repo" not in data:
            env = os.environ.get("AI_FORGE_CAPABILITIES_REPO")
            if env:
                data["capabilities_repo"] = Path(env)
        super().__init__(**data)


def get_settings() -> Settings:
    return Settings()
