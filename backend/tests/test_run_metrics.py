"""Unit tests for per-capability run-metrics aggregation and
CapabilityClient.write_evaluation against the real registry app."""
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api import workflows as wf_module
from app.capability_client import (
    CapabilityClient,
    CapabilityFetchError,
    CapabilityNotFoundError,
)
from app.runs.metrics import _percentile, compute_capability_aggregates
from app.runs.store import _store_connect
from schema.models import (
    Edge,
    HumanInputField,
    HumanInLoopNodeConfig,
    Node,
    StateField,
    StateFieldType,
    StateSchema,
    Workflow,
)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _seed_run(run_id: str, status: str, started_at: float, completed_at: float | None = None,
              cost: float = 0.0, usage=None) -> None:
    """Insert a run row directly into the store DB (usage: dict or raw JSON str)."""
    from app.config import get_settings

    raw = usage if isinstance(usage, str) else (json.dumps(usage) if usage is not None else None)
    conn = _store_connect(str(get_settings().checkpoint_db))
    try:
        conn.execute(
            "INSERT INTO runs (run_id, workflow_id, status, estimated_cost_usd, "
            "capability_usage, started_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, "wf-metrics", status, cost, raw, started_at, completed_at),
        )
        conn.commit()
    finally:
        conn.close()


def _manifest(name="acme/wf", version="1.0.0"):
    from schema.models import Workflow

    return {
        "name": name, "version": version,
        "description": "demo workflow",
        "tags": ["demo"], "kind": "workflow",
        "spec": {"kind": "workflow", "workflow": Workflow(id="w", name="w").model_dump()},
        "interface": {"type": "ai_forge_workflow"},
        "governance": {"owner": "acme"},
    }


# ─── _percentile (nearest-rank) ──────────────────────────────────────────────

def test_percentile_single_value_is_itself():
    assert _percentile([42.0], 0.50) == 42.0
    assert _percentile([42.0], 0.95) == 42.0


def test_percentile_two_values():
    assert _percentile([1000.0, 4000.0], 0.50) == 1000.0
    assert _percentile([1000.0, 4000.0], 0.95) == 4000.0


def test_percentile_known_vector():
    values = [float(i) for i in range(1, 11)]
    assert _percentile(values, 0.50) == 5.0   # ceil(5) - 1 → idx 4
    assert _percentile(values, 0.95) == 10.0  # ceil(9.5) - 1 → idx 9


# ─── aggregation math (hand-computed) ────────────────────────────────────────

def test_aggregates_exact_values():
    # alpha@1.0.0: r1 ok (2000ms, $0.004), r2 failed (5000ms, $0.010),
    #               r3 ok (1000ms, $0.002) → score 2/3, p50 2000, p95 5000
    _seed_run("r1", "completed", 1000.0, 1002.0, 0.004,
              {"acme/alpha": "1.0.0", "acme/beta": None})
    _seed_run("r2", "failed", 2000.0, 2005.0, 0.010, {"acme/alpha": "1.0.0"})
    _seed_run("r3", "completed", 3000.0, 3001.0, 0.002,
              {"acme/alpha": "1.0.0", "acme/delta": "0.1.0"})
    # beta@null (name-only): r1 ok + r4 ok but completed_at NULL → counted in
    # totals/cost, excluded from durations → p50 == p95 == 2000
    _seed_run("r4", "completed", 4000.0, None, 0.006, {"acme/beta": None})
    # gamma@2.0.0: single run → p50 == p95 == that duration
    _seed_run("r5", "completed", 5000.0, 5003.5, 0.0125, {"acme/gamma": "2.0.0"})
    # delta@0.1.0: two runs, one failed → score 0.5
    _seed_run("r6", "failed", 6000.0, 6004.0, 0.008, {"acme/delta": "0.1.0"})
    # non-terminal and usage-less rows must not contribute
    _seed_run("r7", "running", 7000.0, None, 9.999, {"acme/alpha": "1.0.0"})
    _seed_run("r9", "completed", 8000.0, 8001.0, 9.999)

    agg = compute_capability_aggregates()
    assert set(agg) == {
        ("acme/alpha", "1.0.0"), ("acme/beta", None),
        ("acme/gamma", "2.0.0"), ("acme/delta", "0.1.0"),
    }

    alpha = agg[("acme/alpha", "1.0.0")]
    assert alpha["score"] == 0.6667
    assert alpha["stats"] == {
        "runs_total": 3, "runs_failed": 1,
        "duration_ms_p50": 2000.0, "duration_ms_p95": 5000.0,
        "avg_cost_usd": 0.005333,  # (0.004 + 0.010 + 0.002) / 3
    }

    beta = agg[("acme/beta", None)]
    assert beta["score"] == 1.0
    assert beta["stats"] == {
        "runs_total": 2, "runs_failed": 0,
        "duration_ms_p50": 2000.0, "duration_ms_p95": 2000.0,
        "avg_cost_usd": 0.005,  # (0.004 + 0.006) / 2
    }

    gamma = agg[("acme/gamma", "2.0.0")]
    assert gamma["score"] == 1.0
    assert gamma["stats"] == {
        "runs_total": 1, "runs_failed": 0,
        "duration_ms_p50": 3500.0, "duration_ms_p95": 3500.0,
        "avg_cost_usd": 0.0125,
    }

    delta = agg[("acme/delta", "0.1.0")]
    assert delta["score"] == 0.5
    assert delta["stats"] == {
        "runs_total": 2, "runs_failed": 1,
        "duration_ms_p50": 1000.0, "duration_ms_p95": 4000.0,
        "avg_cost_usd": 0.005,  # (0.002 + 0.008) / 2
    }


def test_aggregates_empty_store():
    assert compute_capability_aggregates() == {}


# ─── defensive handling ──────────────────────────────────────────────────────

def test_malformed_capability_usage_is_skipped():
    _seed_run("r1", "completed", 1000.0, 1002.0, 0.004, {"acme/alpha": "1.0.0"})
    _seed_run("r2", "failed", 2000.0, 2005.0, 0.010, "{not valid json")
    _seed_run("r3", "completed", 3000.0, 3001.0, 0.002, "[1, 2, 3]")  # JSON but not a dict

    agg = compute_capability_aggregates()
    assert set(agg) == {("acme/alpha", "1.0.0")}
    assert agg[("acme/alpha", "1.0.0")]["stats"]["runs_total"] == 1


# ─── CapabilityClient.write_evaluation (real registry app) ──────────────────

def test_write_evaluation_success_and_404(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_FORGE_REGISTRY_DB", str(tmp_path / "registry.db"))
    monkeypatch.setenv("AI_FORGE_CAPABILITIES_REPO", str(tmp_path / "caps"))
    from registry.main import app as registry_app

    with TestClient(registry_app) as reg:
        r = reg.post("/registry/capabilities", json=_manifest())
        assert r.status_code == 201, r.text

        # Route the client's sync httpx.put at the real registry app.
        from urllib.parse import urlsplit

        def fake_put(url, **kwargs):
            resp = reg.put(urlsplit(str(url)).path, json=kwargs.get("json"))
            return httpx.Response(resp.status_code, json=resp.json())

        monkeypatch.setattr(httpx, "put", fake_put)
        client = CapabilityClient(base_url="http://127.0.0.1:3010")

        payload = {
            "suite_id": "smoke",
            "last_scored_at": 1700000000.0,
            "score": 0.6667,
            "stats": {"runs_total": 3, "runs_failed": 1,
                      "duration_ms_p50": 2000.0, "duration_ms_p95": 5000.0,
                      "avg_cost_usd": 0.005333},
        }
        result = client.write_evaluation("acme/wf", "1.0.0", payload)
        assert result["ok"] is True
        assert result["evaluation"] == payload

        # The registry actually stored it on the version's manifest.
        detail = reg.get("/registry/capabilities/acme/wf").json()
        assert detail["versions"][0]["manifest"]["evaluation"] == payload

        # Unknown name@version → 404, same error style as use().
        with pytest.raises(CapabilityNotFoundError):
            client.write_evaluation("acme/ghost", "9.9.9", {"score": 1.0})


# ─── terminal hook: report_run_metrics integration ───────────────────────────

def _wait_for_status(client: TestClient, run_id: str, status: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] == status:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} never reached '{status}' (last: {body['status']})")


def _wait_for_pushes(pushed: list, count: int, timeout: float = 10.0) -> None:
    """Terminal pushes fire after the status flips; poll until they land."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(pushed) >= count:
            return
        time.sleep(0.05)
    raise AssertionError(f"expected {count} evaluation push(es), got {pushed}")


class _FakeRegistry:
    """In-memory stand-in for the registry's GET /use endpoint."""

    def __init__(self):
        self.store: dict[str, dict[str, dict]] = {}  # name -> version -> use response

    def add(self, name: str, sub: Workflow, version: str) -> None:
        self.store.setdefault(name, {})[version] = {
            "name": name, "version": version, "kind": "workflow",
            "artifact": sub.model_dump(),
        }

    def use(self, name: str, version: str = "latest") -> dict:
        versions = self.store.get(name)
        if not versions or (version != "latest" and version not in versions):
            raise CapabilityNotFoundError(
                f"capability '{name}' version '{version}' not found (or unpublished)")
        return versions[version] if version != "latest" else versions[max(versions)]


def _sub(hil: bool = False, hil_timeout: int | None = None) -> Workflow:
    """start → cf → [hil] → end."""
    nodes = [
        Node(id="start", type="start", config={}),
        Node(id="cf", type="custom_function",
             config={"code": 'result["y"] = state["data"]["x"] + 1', "output_fields": ["y"]}),
    ]
    edges = [Edge(id="e-cf", source_node_id="start", source_handle="default", target_node_id="cf")]
    prev = "cf"
    if hil:
        nodes.append(Node(id="hil", type="human_in_loop",
                          config=HumanInLoopNodeConfig(
                              input_fields=[HumanInputField(name="note", label="Note", type="text")],
                              output_fields=["note"], timeout_seconds=hil_timeout)))
        edges.append(Edge(id="e-hil", source_node_id=prev, source_handle="default", target_node_id="hil"))
        prev = "hil"
    nodes.append(Node(id="end", type="end", config={}))
    edges.append(Edge(id="e-end", source_node_id=prev, source_handle="default", target_node_id="end"))
    return Workflow(
        id="wf-sub", name="sub",
        state_schema=StateSchema(fields=[StateField(name="x", type=StateFieldType.NUMBER, required=True)]),
        nodes=nodes, edges=edges,
    )


def _stamped_models_tools() -> tuple[list[dict], list[dict]]:
    """Workflow models/tools stamped with registry provenance (one name-only)."""
    models = [
        {"id": "m1", "name": "Llama", "provider": "openai_compatible", "model": "llama-3",
         "source_capability": "acme/llama-profile", "source_version": "0.9.0"},
    ]
    tools = [
        {"id": "t1", "name": "search", "description": "Search things", "parameters": {},
         "implementation": {"type": "builtin", "config": {}},
         "source_capability": "acme/search-tool", "source_version": "1.2.3"},
        {"id": "t2", "name": "calc", "description": "Calculate", "parameters": {},
         "implementation": {"type": "builtin", "config": {}},
         "source_capability": "acme/calc-tool", "source_version": None},
    ]
    return models, tools


def _invoke_wf_json(wf_id: str) -> dict:
    """start → invoke(acme/sub) → end, with provenance-stamped models/tools."""
    models, tools = _stamped_models_tools()
    return {
        "id": wf_id, "name": wf_id,
        "models": models, "tools": tools,
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "inv", "type": "invoke",
             "config": {"capability": "acme/sub", "version": "latest",
                        "input_mapping": [{"source": "data.x", "target": "x"}],
                        "output_field": "sub"}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "s", "source_node_id": "start", "source_handle": "default", "target_node_id": "inv"},
            {"id": "e", "source_node_id": "inv", "source_handle": "default", "target_node_id": "end"},
        ],
    }


def _plain_wf_json(wf_id: str) -> dict:
    """Legacy workflow: no invoke nodes, no models/tools, no provenance."""
    return {
        "id": wf_id, "name": wf_id,
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "cf", "type": "custom_function",
             "config": {"code": 'result["v"] = 1', "output_fields": ["v"]}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "e1", "source_node_id": "start", "source_handle": "default", "target_node_id": "cf"},
            {"id": "e2", "source_node_id": "cf", "source_handle": "default", "target_node_id": "end"},
        ],
    }


def _failing_wf_json(wf_id: str) -> dict:
    """start → cf (raises) → end, with provenance-stamped models/tools."""
    models, tools = _stamped_models_tools()
    return {
        "id": wf_id, "name": wf_id,
        "models": models, "tools": tools,
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "cf", "type": "custom_function",
             "config": {"code": 'raise ValueError("boom")', "output_fields": ["v"]}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "e1", "source_node_id": "start", "source_handle": "default", "target_node_id": "cf"},
            {"id": "e2", "source_node_id": "cf", "source_handle": "default", "target_node_id": "end"},
        ],
    }


@pytest.fixture(autouse=True)
def _clear_runs():
    from app.runs.api import RUNS
    RUNS.clear()
    yield
    RUNS.clear()


@pytest.fixture()
def hook_client(tmp_path, monkeypatch):
    """App with on-disk workflows, a fake registry behind /use, and a capturing
    write_evaluation so the terminal pushes can be asserted."""
    from app import runs as runs_module

    monkeypatch.setattr(wf_module.settings, "workflows_dir", tmp_path)
    reg = _FakeRegistry()
    monkeypatch.setattr(CapabilityClient, "use",
                        lambda self, name, version="latest": reg.use(name, version))
    pushed: list[tuple[str, str, dict]] = []

    def fake_write_evaluation(self, name, version, payload):
        pushed.append((name, version, payload))
        return {"ok": True}

    monkeypatch.setattr(CapabilityClient, "write_evaluation", fake_write_evaluation)
    runs_module.flush_store()

    with TestClient(app) as client:
        yield client, reg, pushed


def _start_run(client: TestClient, wf_id: str, payload: dict | None = None) -> str:
    resp = client.post(f"/api/workflows/{wf_id}/run", json=payload if payload is not None else {"x": 1})
    assert resp.status_code == 202, resp.text
    return resp.json()["run_id"]


def test_terminal_hook_pushes_versioned_capabilities(hook_client):
    """A completed run pushes exactly its versioned capabilities; name-only
    (null-version) entries are never pushed."""
    client, reg, pushed = hook_client
    reg.add("acme/sub", _sub(), "1.0.0")
    (wf_module.settings.workflows_dir / "hook-wf.json").write_text(json.dumps(_invoke_wf_json("hook-wf")))

    run_id = _start_run(client, "hook-wf")
    body = _wait_for_status(client, run_id, "completed")
    assert body["capability_usage"] == {
        "acme/llama-profile": "0.9.0",
        "acme/search-tool": "1.2.3",
        "acme/calc-tool": None,
        "acme/sub": "1.0.0",
    }

    _wait_for_pushes(pushed, 3)
    by_key = {(name, version): payload for name, version, payload in pushed}
    assert set(by_key) == {
        ("acme/llama-profile", "0.9.0"), ("acme/search-tool", "1.2.3"), ("acme/sub", "1.0.0"),
    }

    sub_payload = by_key[("acme/sub", "1.0.0")]
    assert set(sub_payload) == {"score", "last_scored_at", "stats"}
    assert sub_payload["score"] == 1.0
    assert isinstance(sub_payload["last_scored_at"], float)
    stats = sub_payload["stats"]
    assert stats["runs_total"] == 1 and stats["runs_failed"] == 0
    assert stats["duration_ms_p50"] is not None
    assert stats["avg_cost_usd"] == 0.0


def test_terminal_hook_survives_registry_down(hook_client, monkeypatch):
    """A registry outage during the terminal push must not affect the run."""
    client, reg, _pushed = hook_client

    def boom(self, name, version, payload):
        raise CapabilityFetchError("registry unreachable")

    monkeypatch.setattr(CapabilityClient, "write_evaluation", boom)
    reg.add("acme/sub", _sub(), "1.0.0")
    (wf_module.settings.workflows_dir / "hook-wf.json").write_text(json.dumps(_invoke_wf_json("hook-wf")))

    run_id = _start_run(client, "hook-wf")
    body = _wait_for_status(client, run_id, "completed")
    assert body["status"] == "completed"
    assert body["events"][-1]["type"] == "run_end"


def test_terminal_hook_noop_without_capability_usage(hook_client):
    """A legacy run (capability_usage NULL) triggers zero pushes."""
    client, _reg, pushed = hook_client
    (wf_module.settings.workflows_dir / "plain-wf.json").write_text(json.dumps(_plain_wf_json("plain-wf")))

    run_id = _start_run(client, "plain-wf", payload={})
    body = _wait_for_status(client, run_id, "completed")
    assert body["capability_usage"] is None

    time.sleep(0.2)  # let any (erroneous) background push settle
    assert pushed == []


def test_terminal_hook_pushes_for_failed_run(hook_client):
    """A failed run also reports; the failure shows up in score/stats."""
    client, _reg, pushed = hook_client
    (wf_module.settings.workflows_dir / "fail-wf.json").write_text(json.dumps(_failing_wf_json("fail-wf")))

    run_id = _start_run(client, "fail-wf", payload={})
    body = _wait_for_status(client, run_id, "failed")
    assert "boom" in (body["error"] or "")

    _wait_for_pushes(pushed, 2)  # no invoke pin here: model + one versioned tool only
    by_key = {(name, version): payload for name, version, payload in pushed}
    assert set(by_key) == {("acme/llama-profile", "0.9.0"), ("acme/search-tool", "1.2.3")}
    payload = by_key[("acme/llama-profile", "0.9.0")]
    assert payload["score"] == 0.0
    assert payload["stats"]["runs_total"] == 1
    assert payload["stats"]["runs_failed"] == 1


def test_terminal_hook_pushes_on_human_timeout(hook_client):
    """The human-timeout expiry (sync failed path) reports too."""
    client, reg, pushed = hook_client
    reg.add("acme/sub", _sub(hil=True, hil_timeout=1), "1.0.0")
    (wf_module.settings.workflows_dir / "hook-wf.json").write_text(json.dumps(_invoke_wf_json("hook-wf")))

    run_id = _start_run(client, "hook-wf")
    _wait_for_status(client, run_id, "paused")
    body = _wait_for_status(client, run_id, "failed", timeout=5)
    assert "timed out" in (body["error"] or "")
    assert body["events"][-1]["type"] == "human_timeout"

    _wait_for_pushes(pushed, 3)
    by_key = {(name, version): payload for name, version, payload in pushed}
    assert set(by_key) == {
        ("acme/llama-profile", "0.9.0"), ("acme/search-tool", "1.2.3"), ("acme/sub", "1.0.0"),
    }
    assert by_key[("acme/sub", "1.0.0")]["stats"]["runs_failed"] == 1
