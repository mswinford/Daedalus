from fastapi import APIRouter, HTTPException
from pathlib import Path
import json
import uuid

from schema.models import Workflow
from app.config import get_settings
from app.engine.validation import validate_workflow

router = APIRouter()
settings = get_settings()


def _load_workflow(workflow_id: str) -> Workflow:
    path = settings.workflows_dir / f"{workflow_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")
    data = json.loads(path.read_text())
    return Workflow.model_validate(data)


def _save_workflow(workflow: Workflow) -> None:
    path = settings.workflows_dir / f"{workflow.id}.json"
    path.write_text(workflow.model_dump_json(indent=2))


@router.get("/workflows")
async def list_workflows():
    """List all workflows."""
    workflows = []
    for path in settings.workflows_dir.glob("*.json"):
        data = json.loads(path.read_text())
        workflows.append({
            "id": data["id"],
            "name": data["name"],
            "description": data.get("description"),
        })
    return workflows


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str):
    """Get a workflow by ID."""
    return _load_workflow(workflow_id)


@router.post("/workflows/{workflow_id}/validate")
async def validate_endpoint(workflow_id: str, workflow: Workflow | None = None):
    """Dry-run structural validation of a workflow (no execution).

    If a workflow body is provided, validates that (unsaved canvas state).
    Otherwise falls back to the saved file on disk.
    """
    if workflow is None:
        workflow = _load_workflow(workflow_id)  # 404 if missing
    else:
        workflow.id = workflow_id
    result = validate_workflow(workflow)
    return result.model_dump()


@router.post("/workflows", status_code=201)
async def create_workflow(workflow: Workflow):
    """Create a new workflow."""
    # Override ID if not provided
    if not workflow.id:
        workflow.id = str(uuid.uuid4())
    _save_workflow(workflow)
    return workflow


@router.put("/workflows/{workflow_id}")
async def update_workflow(workflow_id: str, workflow: Workflow):
    """Update an existing workflow."""
    # Ensure IDs match
    workflow.id = workflow_id
    _save_workflow(workflow)
    return workflow


@router.delete("/workflows/{workflow_id}", status_code=204)
async def delete_workflow(workflow_id: str):
    """Delete a workflow."""
    path = settings.workflows_dir / f"{workflow_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")
    path.unlink()
