from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.api import workflows, runs


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    print(f"AI Forge starting on {settings.host}:{settings.port}")
    print(f"Data directory: {settings.data_dir}")
    yield
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


@app.get("/health")
async def health():
    return {"status": "ok"}
