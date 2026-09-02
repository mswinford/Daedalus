"""Bundled workflow templates: list + fetch, for creating new workflows from a template.

Templates are plain workflow JSON files shipped inside the app package
(``app/templates/*.json``); they are read-only — instantiation is a normal
``POST /workflows`` with a fresh id, done by the client.
"""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

router = APIRouter(tags=["templates"])


def _load_all() -> list[dict]:
    templates: list[dict] = []
    for path in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(doc, dict) and doc.get("id"):
            templates.append(doc)
    return templates


@router.get("/templates")
async def list_templates():
    return [
        {
            "id": t["id"],
            "name": t.get("name") or t["id"],
            "description": t.get("description"),
        }
        for t in _load_all()
    ]


@router.get("/templates/{template_id}")
async def get_template(template_id: str):
    for t in _load_all():
        if t["id"] == template_id:
            return t
    raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
