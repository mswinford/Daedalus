"""Full-text search endpoint."""
from fastapi import APIRouter, Request

from registry.store import search

router = APIRouter()


@router.get("/search")
async def search_endpoint(request: Request, q: str = "", kind: str | None = None, limit: int = 50):
    results = await search(request.app.state.db, q, kind=kind, limit=limit)
    return {"results": results}
