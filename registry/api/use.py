"""Use endpoint — returns the consumable artifact payload for a version."""
from fastapi import APIRouter, HTTPException, Request

from registry.store import get_artifact

router = APIRouter()


@router.get("/capabilities/{name:path}/use")
async def use(name: str, request: Request, version: str = "latest"):
    db = request.app.state.db
    try:
        return await get_artifact(db, name, version)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
