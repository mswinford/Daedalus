"""Use endpoint — returns the consumable artifact payload for a version.

With `inline=true`, capability refs inside skill/agent specs are resolved
against the registry and embedded, so the returned artifact is fully
self-contained (R1 import is inline).
"""
from fastapi import APIRouter, HTTPException, Request

from registry.inline import InliningError, inline_artifact
from registry.store import get_artifact

router = APIRouter()


@router.get("/capabilities/{name:path}/use")
async def use(
    name: str, request: Request, version: str = "latest", inline: bool = False
):
    db = request.app.state.db
    try:
        if inline:
            return await inline_artifact(db, name, version)
        return await get_artifact(db, name, version)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InliningError as e:
        raise HTTPException(status_code=422, detail=str(e))
