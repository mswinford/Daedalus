"""JSON file-based persistence for workflows."""
import json
from pathlib import Path
from typing import Optional

from schema.models import Workflow
from app.config import get_settings


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
        return Workflow.model_validate(data)

    def save(self, workflow: Workflow) -> Workflow:
        """Save a workflow."""
        path = self.workflows_dir / f"{workflow.id}.json"
        path.write_text(workflow.model_dump_json(indent=2))
        return workflow

    def delete(self, workflow_id: str) -> bool:
        """Delete a workflow."""
        path = self.workflows_dir / f"{workflow_id}.json"
        if not path.exists():
            return False
        path.unlink()
        return True
