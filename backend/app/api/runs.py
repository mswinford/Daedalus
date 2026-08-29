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
    events: list[RunEvent] = []
    run = WorkflowRun(
        id=run_id,
        workflow_id=workflow_id,
        status=RunStatus.RUNNING,
        input_data=input_data,
        started_at=time.time(),
    )

    try:
        result = run_workflow_sync(workflow, input_data, trace=events)
        run.status = RunStatus.COMPLETED
        run.output_data = {
            "output": result.get("output", ""),
            "messages": result.get("messages", []),
            "data": result.get("data", {}),
            "node_outputs": result.get("node_outputs", {}),
        }
        run.total_tokens_input = result.get("total_tokens_input", 0)
        run.total_tokens_output = result.get("total_tokens_output", 0)
        run.estimated_cost_usd = result.get("estimated_cost_usd", 0.0)
    except Exception as e:
        run.status = RunStatus.FAILED
        run.error = str(e)
        # A node_error may already be in the trace from the failing node; only
        # add one if the failure happened outside a node (e.g. input validation).
        if not any(ev.type == "node_error" for ev in events):
            events.append(RunEvent(type="node_error", data={"error": str(e)}, timestamp=time.time()))

    run.events = events
    run.completed_at = time.time()
    return run
