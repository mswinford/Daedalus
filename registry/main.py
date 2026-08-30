from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from registry.api import capabilities, publish, search, use
from registry.config import get_settings
from registry.db import Database
from registry.indexer import sync_from_repo


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    print(f"AI Forge Registry starting on {settings.host}:{settings.port}")
    print(f"Index: {settings.registry_db}")
    print(f"Capabilities repo: {settings.capabilities_repo}")
    app.state.db = await Database.connect(settings.registry_db)
    report = await sync_from_repo(settings.capabilities_repo, app.state.db)
    if report["synced"]:
        print(f"Indexed {report['synced']} new capability version(s) on start")
    yield
    await app.state.db.close()
    print("Registry shutting down...")


app = FastAPI(
    title="AI Forge Registry",
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
