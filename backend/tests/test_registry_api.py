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


def test_publish_git_db_agreement(tmp_path, monkeypatch):
    """After publish, the DB row's source_commit is the repo HEAD and the
    manifest file exists at <repo>/<name>/<version>/manifest.json."""
    import subprocess

    with _client(tmp_path, monkeypatch) as client:
        r = _publish(client, _manifest())
        assert r.status_code == 201, r.text

        head = subprocess.run(
            ["git", "-C", str(tmp_path / "caps"), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        r = client.get("/registry/capabilities/acme/wf")
        assert r.status_code == 200
        version = r.json()["versions"][0]
        assert version["source_commit"] == head

        manifest_file = tmp_path / "caps" / "acme" / "wf" / "1.0.0" / "manifest.json"
        assert manifest_file.exists()


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


def _evaluation():
    return {
        "suite_id": "nightly-2026",
        "last_scored_at": 1750000000.0,
        "score": 0.93,
        "stats": {
            "runs_total": 120,
            "runs_failed": 8,
            "duration_ms_p50": 412.5,
            "duration_ms_p95": 900.0,
            "avg_cost_usd": 0.004,
        },
    }


def _publish_to_published(client):
    _publish(client, _manifest())
    for stage in ("review", "approved", "published"):
        r = client.post(
            "/registry/capabilities/acme/wf/lifecycle",
            json={"version": "1.0.0", "stage": stage},
        )
        assert r.status_code == 200, r.text


def test_set_evaluation_roundtrip(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _publish_to_published(client)

        r = client.put(
            "/registry/capabilities/acme/wf/versions/1.0.0/evaluation",
            json=_evaluation(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True and body["name"] == "acme/wf"
        assert body["evaluation"]["score"] == 0.93

        # detail response now includes it (score + stats round-trip)
        r = client.get("/registry/capabilities/acme/wf")
        ev = r.json()["versions"][0]["manifest"]["evaluation"]
        assert ev["suite_id"] == "nightly-2026"
        assert ev["last_scored_at"] == 1750000000.0
        assert ev["score"] == 0.93
        assert ev["stats"]["runs_total"] == 120
        assert ev["stats"]["duration_ms_p95"] == 900.0

        # the use endpoint merges it into its manifest too
        r = client.get("/registry/capabilities/acme/wf/use")
        assert r.status_code == 200
        assert r.json()["manifest"]["evaluation"]["score"] == 0.93


def test_set_evaluation_unknown_version_404(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _publish(client, _manifest())
        unknown_name = "/registry/capabilities/nope/nothing/versions/1.0.0/evaluation"
        assert client.put(unknown_name, json={}).status_code == 404
        bad_version = "/registry/capabilities/acme/wf/versions/9.9.9/evaluation"
        assert client.put(bad_version, json={"score": 1.0}).status_code == 404


def test_set_evaluation_malformed_body_422(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _publish(client, _manifest())
        url = "/registry/capabilities/acme/wf/versions/1.0.0/evaluation"
        # wrong types are rejected by the CapabilityEvaluationRef body model
        assert client.put(url, json={"score": "high"}).status_code == 422
        assert client.put(url, json={"stats": "not-a-dict"}).status_code == 422


def test_detail_without_evaluation_is_null(tmp_path, monkeypatch):
    """A version with no stored evaluation still carries the manifest key —
    as null (the CapabilityManifest model always dumps it)."""
    with _client(tmp_path, monkeypatch) as client:
        _publish(client, _manifest())
        r = client.get("/registry/capabilities/acme/wf")
        manifest = r.json()["versions"][0]["manifest"]
        assert "evaluation" in manifest and manifest["evaluation"] is None


def test_evaluation_survives_registry_restart(tmp_path, monkeypatch):
    """Evaluation is runtime metadata: a full git->DB resync (what happens on
    every registry start) must not wipe it."""
    with _client(tmp_path, monkeypatch) as client:
        _publish(client, _manifest())
        r = client.put(
            "/registry/capabilities/acme/wf/versions/1.0.0/evaluation",
            json=_evaluation(),
        )
        assert r.status_code == 200, r.text

    # second client on the same DB + repo: lifespan re-syncs from git
    with _client(tmp_path, monkeypatch) as restarted:
        r = restarted.get("/registry/capabilities/acme/wf")
        ev = r.json()["versions"][0]["manifest"]["evaluation"]
        assert ev["score"] == 0.93
        assert ev["stats"]["runs_total"] == 120
