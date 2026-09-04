from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from registry.api import capabilities, publish, search, use
from registry.config import get_settings
from registry.db import Database
from registry.indexer import ensure_repo, pull_for_sync, sync_from_repo


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    print(f"Daedalus Registry starting on {settings.host}:{settings.port}")
    print(f"Index: {settings.registry_db}")
    print(f"Capabilities repo: {settings.capabilities_repo}")
    if settings.capabilities_remote:
        print(f"Capabilities remote: {settings.capabilities_remote}")
    app.state.db = await Database.connect(settings.registry_db)
    if settings.capabilities_remote:
        # Clone on first boot; fetch+rebase so out-of-band pushes (web UI,
        # PRs, another machine) land before the index scan. Best-effort —
        # an offline boot still indexes the local state.
        await ensure_repo(settings.capabilities_repo, settings.capabilities_remote)
        if not await pull_for_sync(settings.capabilities_repo, settings.capabilities_remote):
            print("warning: capabilities repo not synced with remote; "
                  "indexing local state")
    report = await sync_from_repo(settings.capabilities_repo, app.state.db)
    if report["synced"]:
        print(f"Indexed {report['synced']} new capability version(s) on start")
    yield
    await app.state.db.close()
    print("Registry shutting down...")


app = FastAPI(
    title="Daedalus Registry",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Order matters: the {name:path} detail route swallows everything under
# /capabilities/, so more specific routes must be registered first.
app.include_router(publish.router, prefix="/registry", tags=["capabilities"])
app.include_router(use.router, prefix="/registry", tags=["capabilities"])
app.include_router(search.router, prefix="/registry", tags=["search"])
app.include_router(capabilities.router, prefix="/registry", tags=["capabilities"])


@app.get("/health")
async def health():
    return {"status": "ok"}
