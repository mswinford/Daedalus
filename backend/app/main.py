from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.api import workflows, secrets
from app import runs


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    print(f"AI Forge starting on {settings.host}:{settings.port}")
    print(f"Data directory: {settings.data_dir}")
    recovered = await runs.recover_paused_runs()
    if recovered:
        print(f"Recovered {recovered} paused run(s) from the checkpoint store")
    finished = await runs.recover_finished_runs()
    if finished:
        print(f"Restored {finished} finished run(s) from the checkpoint store")
    yield
    runs.shutdown_store()
    print("Shutting down...")


app = FastAPI(
    title="AI Forge",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(workflows.router, prefix="/api", tags=["workflows"])
app.include_router(runs.router, prefix="/api", tags=["runs"])
app.include_router(secrets.router, prefix="/api", tags=["secrets"])


@app.get("/health")
async def health():
    return {"status": "ok"}
