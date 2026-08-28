"""Generate JSON Schema from Pydantic models."""
import json
from schema.models import Workflow, WorkflowRun

if __name__ == "__main__":
    schema = Workflow.model_json_schema()
    with open("schema/workflow_schema.json", "w") as f:
        json.dump(schema, f, indent=2)
    print("Generated schema/workflow_schema.json")

    run_schema = WorkflowRun.model_json_schema()
    with open("schema/run_schema.json", "w") as f:
        json.dump(run_schema, f, indent=2)
    print("Generated schema/run_schema.json")
