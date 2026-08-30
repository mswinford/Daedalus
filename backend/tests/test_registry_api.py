"""API tests for the registry (TestClient against a temp DB + git repo)."""
from fastapi.testclient import TestClient

from schema.models import Workflow


def _manifest(name="acme/wf", version="1.0.0", **kw):
    base = {
        "name": name, "version": version,
        "description": "Demo workflow that does things",
        "tags": ["demo"],
        "kind": "workflow",
        "spec": {"kind": "workflow", "workflow": Workflow(id="w", name="w").model_dump()},
        "interface": {"type": "ai_forge_workflow"},
        "governance": {"owner": "acme"},
    }
    base.update(kw)
    return base


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_FORGE_REGISTRY_DB", str(tmp_path / "registry.db"))
    monkeypatch.setenv("AI_FORGE_CAPABILITIES_REPO", str(tmp_path / "caps"))
    from registry.main import app
    return TestClient(app)


def _publish(client, manifest):
    return client.post("/registry/capabilities", json=manifest)


def test_publish_then_list_and_detail(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = _publish(client, _manifest())
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "acme/wf" and body["stage"] == "draft"
        assert body["source_commit"]

        # git repo got the file + a commit
        repo_file = tmp_path / "caps" / "acme" / "wf" / "1.0.0" / "manifest.json"
        assert repo_file.exists()
        assert (tmp_path / "caps" / ".git").exists()

        r = client.get("/registry/capabilities")
        names = [c["name"] for c in r.json()["capabilities"]]
        assert names == ["acme/wf"]

        r = client.get("/registry/capabilities/acme/wf")
        assert r.status_code == 200
        versions = r.json()["versions"]
        assert len(versions) == 1 and versions[0]["version"] == "1.0.0"
        assert versions[0]["manifest"]["name"] == "acme/wf"

        assert client.get("/registry/capabilities/nope/nothing").status_code == 404


def test_publish_conflicts(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert _publish(client, _manifest()).status_code == 201
        r = _publish(client, _manifest())
        assert r.status_code == 409 and "already published" in r.json()["detail"]
        r = _publish(client, _manifest(description="changed"))
        assert r.status_code == 409 and "different content" in r.json()["detail"]
        # a new version is fine
        assert _publish(client, _manifest(version="1.1.0")).status_code == 201


def test_publish_invalid_manifest_rejected(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        bad = _manifest()
        del bad["spec"]
        assert _publish(client, bad).status_code == 422
        bad = _manifest(name="no-slash-name")
        assert _publish(client, bad).status_code == 422


def test_lifecycle_transitions(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _publish(client, _manifest())
        url = "/registry/capabilities/acme/wf/lifecycle"

        assert client.post(url, json={"version": "1.0.0", "stage": "published"}).status_code == 409
        r = client.post(url, json={"version": "1.0.0", "stage": "review"})
        assert r.status_code == 200 and r.json()["stage"] == "review"
        r = client.post(url, json={"version": "1.0.0", "stage": "bogus"})
        assert r.status_code == 422
        assert client.post(url, json={"version": "9.9.9", "stage": "review"}).status_code == 404


def test_use_requires_published(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _publish(client, _manifest())
        r = client.get("/registry/capabilities/acme/wf/use")
        assert r.status_code == 404 and "no published version" in r.json()["detail"]

        client.post("/registry/capabilities/acme/wf/lifecycle",
                    json={"version": "1.0.0", "stage": "review"})
        client.post("/registry/capabilities/acme/wf/lifecycle",
                    json={"version": "1.0.0", "stage": "approved"})
        client.post("/registry/capabilities/acme/wf/lifecycle",
                    json={"version": "1.0.0", "stage": "published"})

        r = client.get("/registry/capabilities/acme/wf/use")
        assert r.status_code == 200
        body = r.json()
        assert body["version"] == "1.0.0"
        assert body["artifact"]["id"] == "w"
        assert body["manifest"]["kind"] == "workflow"

        # explicit version works too; unknown does not
        assert client.get("/registry/capabilities/acme/wf/use",
                          params={"version": "1.0.0"}).status_code == 200
        assert client.get("/registry/capabilities/acme/wf/use",
                          params={"version": "9.9.9"}).status_code == 404


def test_search_endpoint(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _publish(client, _manifest())
        prompt = {
            "name": "acme/prompts", "version": "1.0.0",
            "description": "Invoice extraction prompt for OCR pipelines",
            "tags": ["invoice"], "kind": "prompt",
            "spec": {"kind": "prompt", "text": "Extract {{fields}}"},
            "governance": {"owner": "acme"},
        }
        _publish(client, prompt)

        r = client.get("/registry/search", params={"q": "invoice"})
        assert [h["name"] for h in r.json()["results"]] == ["acme/prompts"]

        r = client.get("/registry/search", params={"q": "demo", "kind": "workflow"})
        assert [h["name"] for h in r.json()["results"]] == ["acme/wf"]

        assert client.get("/registry/search", params={"q": ""}).json()["results"] == []


def test_list_kind_filter(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _publish(client, _manifest())
        prompt = {
            "name": "acme/prompts", "version": "1.0.0", "description": "d",
            "kind": "prompt", "spec": {"kind": "prompt", "text": "x"},
            "governance": {"owner": "acme"},
        }
        _publish(client, prompt)
        r = client.get("/registry/capabilities", params={"kind": "prompt"})
        assert [c["name"] for c in r.json()["capabilities"]] == ["acme/prompts"]
