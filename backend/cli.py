"""CLI entry point for Daedalus."""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "backend"))


def main():
    import uvicorn
    from app.config import get_settings

    settings = get_settings()
    print(f"Starting Daedalus on {settings.host}:{settings.port}")
    print(f"Workflows directory: {settings.workflows_dir}")

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )


if __name__ == "__main__":
    main()
