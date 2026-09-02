"""Capability listing, detail, and lifecycle endpoints."""
from fastapi import APIRouter, HTTPException, Request

from registry.store import (
    InvalidTransitionError,
    get_versions,
    list_capabilities,
    set_evaluation,
    transition_stage,
)
from schema.capability import CapabilityEvaluationRef, LifecycleStage

router = APIRouter()


@router.get("/capabilities")
async def capabilities(request: Request, kind: str | None = None):
    caps = await list_capabilities(request.app.state.db)
    if kind:
        caps = [c for c in caps if c["kind"] == kind]
    return {"capabilities": caps}


@router.post("/capabilities/{name:path}/lifecycle")
async def lifecycle(name: str, request: Request, body: dict):
    version = body.get("version")
    stage_raw = body.get("stage")
    if not version or not stage_raw:
        raise HTTPException(status_code=422, detail="version and stage are required")
    try:
        stage = LifecycleStage(stage_raw)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"invalid stage {stage_raw!r}; expected one of {[s.value for s in LifecycleStage]}",
        )
    try:
        new_stage = await transition_stage(request.app.state.db, name, version, stage)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"{name}@{version} not found")
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"name": name, "version": version, "stage": new_stage}


@router.put("/capabilities/{name:path}/versions/{version}/evaluation")
async def set_capability_evaluation(
    name: str, version: str, request: Request, body: CapabilityEvaluationRef
):
    """Store runtime evaluation metadata for a version (SQLite only — never
    touches the manifest bytes or git). An all-null body clears it."""
    stored = body.model_dump(mode="json", exclude_none=True) or None
    try:
        await set_evaluation(request.app.state.db, name, version, stored)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"{name}@{version} not found")
    return {"ok": True, "name": name, "version": version, "evaluation": stored}


@router.get("/capabilities/{name:path}")
async def capability_detail(name: str, request: Request):
    try:
        versions = await get_versions(request.app.state.db, name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"capability {name} not found")
    if not versions:
        raise HTTPException(status_code=404, detail=f"capability {name} not found")
    return {"name": name, "versions": [
        {
            **{k: v for k, v in ver.items() if k != "manifest"},
            "manifest": ver["manifest"].model_dump(mode="json"),
        }
        for ver in versions
    ]}
