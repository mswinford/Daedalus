from fastapi import APIRouter, HTTPException
import time
import uuid

from schema.models import WorkflowRun, RunStatus, RunEvent
from app.engine.runner import run_workflow_sync
from app.api.workflows import _load_workflow

router = APIRouter()


@router.post("/workflows/{workflow_id}/run")
def run_workflow(workflow_id: str, input_data: dict = {}):
    """Execute a workflow synchronously (Phase 1)."""
    workflow = _load_workflow(workflow_id)

    run_id = str(uuid.uuid4())
    run = WorkflowRun(
        id=run_id,
        workflow_id=workflow_id,
        status=RunStatus.RUNNING,
        input_data=input_data,
    )

    try:
        result = run_workflow_sync(workflow, input_data)
        run.status = RunStatus.COMPLETED
        run.output_data = result
    except Exception as e:
        run.status = RunStatus.FAILED
        run.error = str(e)
        run.events.append(RunEvent(
            type="node_error",
            data={"error": str(e)},
            timestamp=time.time(),
        ))

    run.completed_at = time.time()
    return run
