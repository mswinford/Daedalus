"""JSON file-based persistence for workflows."""
import json
from pathlib import Path
from typing import Callable, Optional

from schema.models import Workflow
from app.config import get_settings

CURRENT_SCHEMA_VERSION = 1

# Maps version N -> function upgrading a raw dict from N to N+1 (applied ascending).
# Migration #2 will add {2: _migrate_1_to_2} here.
MIGRATIONS: dict[int, Callable[[dict], dict]] = {}


def load_workflow(data: dict) -> Workflow:
    """Validate a raw workflow dict, migrating it to the current schema version."""
    version = data.get("schema_version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError(
            f"invalid workflow schema version {version!r}: expected an integer >= 1"
        )
    if version > CURRENT_SCHEMA_VERSION:
        raise ValueError(f"unsupported workflow schema version {version}")
    for v in range(version, CURRENT_SCHEMA_VERSION):
        data = MIGRATIONS[v](data)
    return Workflow.model_validate(data)


class WorkflowStore:
    """File-based workflow storage."""

    def __init__(self):
        self.workflows_dir = get_settings().workflows_dir
        self.workflows_dir.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[dict]:
        """List all workflows (metadata only)."""
        workflows = []
        for path in self.workflows_dir.glob("*.json"):
            data = json.loads(path.read_text())
            workflows.append({
                "id": data["id"],
                "name": data["name"],
                "description": data.get("description"),
            })
        return workflows

    def get(self, workflow_id: str) -> Optional[Workflow]:
        """Get a workflow by ID."""
        path = self.workflows_dir / f"{workflow_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return load_workflow(data)

    def save(self, workflow: Workflow) -> Workflow:
        """Save a workflow, stamped with the current schema version."""
        path = self.workflows_dir / f"{workflow.id}.json"
        data = workflow.model_dump(mode="json")
        data["schema_version"] = CURRENT_SCHEMA_VERSION
        path.write_text(json.dumps(data, indent=2))
        return workflow

    def delete(self, workflow_id: str) -> bool:
        """Delete a workflow."""
        path = self.workflows_dir / f"{workflow_id}.json"
        if not path.exists():
            return False
        path.unlink()
        return True
