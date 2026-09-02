"""Tests for bundled workflow templates.

Guard (refactoring-plan R13 watch item): every template must load through the
real loader and pass validation — a broken template would only surface when a
user instantiates it. Plus the /api/templates endpoints.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api import templates as templates_module
from app.engine.validation import validate_workflow
from app.persistence.workflows import load_workflow


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _template_files():
    return sorted(templates_module.TEMPLATES_DIR.glob("*.json"))


def test_templates_dir_is_not_empty():
    assert _template_files(), f"No template files in {templates_module.TEMPLATES_DIR}"


def test_every_template_loads_and_validates():
    for path in _template_files():
        doc = json.loads(path.read_text())
        wf = load_workflow(doc)
        result = validate_workflow(wf)
        assert result.valid, f"{path.name}: {[e.message for e in result.errors]}"


def test_github_pr_agent_has_approval_gate_before_pr_tool():
    """The demo workflow's core contract: implementer works without the PR tool;
    a human approval gate sits between implementation and PR creation."""
    doc = json.loads((templates_module.TEMPLATES_DIR / "github-pr-agent.json").read_text())
    wf = load_workflow(doc)
    by_id = {n.id: n for n in wf.nodes}

    assert by_id["approve"].type == "human_in_loop"
    approve_cfg = by_id["approve"].config
    assert approve_cfg.approval_required is True

    implement_cfg = by_id["implement"].config
    assert "github_create_pr" not in implement_cfg.tool_ids

    finalize_cfg = by_id["finalize"].config
    assert "github_create_pr" in finalize_cfg.tool_ids


def test_list_templates(client):
    resp = client.get("/api/templates")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list) and len(body) >= 1
    ids = [t["id"] for t in body]
    assert "github-pr-agent" in ids
    for t in body:
        assert set(t) == {"id", "name", "description"}


def test_get_template_detail(client):
    resp = client.get("/api/templates/github-pr-agent")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["id"] == "github-pr-agent"
    assert len(doc["nodes"]) == 6
    assert len(doc["tools"]) == 4
    tool_ids = {t["id"] for t in doc["tools"]}
    assert {"github_create_branch", "github_read_file", "github_write_file", "github_create_pr"} <= tool_ids


def test_get_template_404(client):
    resp = client.get("/api/templates/nope")
    assert resp.status_code == 404
