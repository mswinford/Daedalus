"""Console entry point for the Daedalus backend server (the ``daedalus`` command).

Boots the FastAPI app via uvicorn with auto-reload. Works both from an
installed wheel (where ``app`` is a top-level module) and from a source
checkout (where we add the import roots to ``sys.path`` first).
"""
import sys
from pathlib import Path


def _bootstrap_path() -> None:
    """Make ``app`` importable when running from a source checkout.

    No-op when installed as a wheel, where ``app`` already lives on the path.
    """
    root = Path(__file__).resolve().parent.parent
    for p in (root, root / "backend"):
        sp = str(p)
        if sp not in sys.path:
            sys.path.insert(0, sp)


def main() -> None:
    _bootstrap_path()

    import uvicorn

    from app.config import get_settings

    settings = get_settings()
    print(f"Daedalus starting on {settings.host}:{settings.port}")
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)


if __name__ == "__main__":
    main()
