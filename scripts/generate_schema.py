"""Generate JSON Schema from Pydantic models."""
import json
from schema.capability import CapabilityManifest
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

    cap_schema = CapabilityManifest.model_json_schema()
    with open("schema/capability_schema.json", "w") as f:
        json.dump(cap_schema, f, indent=2)
    print("Generated schema/capability_schema.json")
