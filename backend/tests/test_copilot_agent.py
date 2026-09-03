"""Tests for the copilot_agent node (phase 1).

The Copilot SDK is never touched here: a fake runtime is injected behind the
create_copilot_runtime seam, so the whole suite runs without auth or network.
Live e2e is gated on COPILOT_SDK_LIVE=1 (separate script, not this module).
"""
import asyncio
import os

import pytest

from app.engine.copilot import (
    CopilotNoResponseError,
    CopilotResult,
    CopilotSessionError,
    CopilotTimeoutError,
    ToolCallRecord,
)
from app.engine.runner import run_workflow_sync
from app.engine.validation import validate_workflow
from schema.models import Edge, Node, Workflow


class FakeRuntime:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    async def run_task(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def fake_runtime(monkeypatch):
    inst = FakeRuntime()
    monkeypatch.setattr(
        "app.engine.nodes.copilot_agent.create_copilot_runtime", lambda: inst
    )
    return inst


def _wf(task="Do the thing", **config_overrides):
    config = {"task": task, **config_overrides}
    return Workflow(
        id="wf-cp", name="cp",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="cp", type="copilot_agent", config=config),
            Node(id="end", type="end", config={}),
        ],
        edges=[
            Edge(id="e1", source_node_id="start", source_handle="default", target_node_id="cp"),
            Edge(id="e2", source_node_id="cp", source_handle="default", target_node_id="end"),
        ],
    )


def _result(message="Done.", **kw):
    return CopilotResult(final_message=message, **kw)


# ─── Happy path ──────────────────────────────────────────────────────────────

def test_happy_path(fake_runtime):
    fake_runtime.result = _result(
        model="gpt-5", tokens_input=100, tokens_output=50, cost_usd=0.01,
        tool_calls=[ToolCallRecord(id="t1", name="search", args={"q": "x"}, success=True)],
    )
    out = run_workflow_sync(_wf(), {}, thread_id="run-1")

    assert out["output"] == "Done."
    node_out = out["node_outputs"]["cp"]
    assert node_out["final_message"] == "Done."
    assert node_out["model"] == "gpt-5"
    assert node_out["tokens_input"] == 100
    assert node_out["tokens_output"] == 50
    assert node_out["cost_usd"] == 0.01
    assert node_out["tool_calls"][0]["name"] == "search"
    # default output_fields → final_message lands in data
    assert out["data"]["final_message"] == "Done."

    call = fake_runtime.calls[0]
    assert call["task"].startswith("Do the thing")
    assert "GITHUB_TOKEN" in call["task"]  # environment note appended
    assert call["model"] is None
    assert call["permission_policy"] == "safe_only"
    assert call["timeout_seconds"] is None
    assert call["github_token"] is None


def test_task_template_rendering(fake_runtime):
    fake_runtime.result = _result()
    run_workflow_sync(_wf(task="Score is {{data.score}}. Verdict: {{data.verdict}}"),
                      {"score": 87, "verdict": "pass"})
    assert fake_runtime.calls[0]["task"].startswith("Score is 87. Verdict: pass")


def test_output_fields_mapping(fake_runtime):
    fake_runtime.result = _result(model="gpt-5")
    out = run_workflow_sync(
        _wf(output_fields=["final_message", "model"]), {}, thread_id="run-2"
    )
    assert out["data"]["final_message"] == "Done."
    assert out["data"]["model"] == "gpt-5"


# ─── Failure rules ───────────────────────────────────────────────────────────

def test_no_response_fails_run(fake_runtime, monkeypatch):
    fake_runtime.error = CopilotNoResponseError(
        "Copilot runtime returned no response — check GitHub auth and Copilot subscription"
    )
    trace: list = []
    with pytest.raises(Exception, match="no response"):
        run_workflow_sync(_wf(), {}, thread_id="run-3", trace=trace)
    assert any(e.type == "node_error" for e in trace)


def test_timeout_fails_run(fake_runtime):
    fake_runtime.error = CopilotTimeoutError("Copilot session exceeded 30s")
    with pytest.raises(Exception, match="exceeded 30s"):
        run_workflow_sync(_wf(timeout_seconds=30), {}, thread_id="run-4")


def test_session_error_fails_run(fake_runtime):
    fake_runtime.error = CopilotSessionError("auth_failed", "not signed in", status_code=401)
    with pytest.raises(Exception, match="auth_failed"):
        run_workflow_sync(_wf(), {}, thread_id="run-5")


# ─── Events ──────────────────────────────────────────────────────────────────

def test_tool_events_emitted_via_on_event(fake_runtime, monkeypatch):
    """A runtime that fires on_event produces tool_call/tool_result run events."""
    async def run_task_with_events(**kwargs):
        kwargs["on_event"]("tool_call", {"name": "edit_file", "args": {"path": "a.txt"}})
        kwargs["on_event"]("tool_result", {"name": "edit_file", "success": True})
        return _result()

    fake_runtime.run_task = run_task_with_events
    trace: list = []
    run_workflow_sync(_wf(), {}, thread_id="run-7", trace=trace)

    kinds = [e.type for e in trace]
    assert kinds.count("tool_call") == 1
    assert kinds.count("tool_result") == 1
    tc = next(e for e in trace if e.type == "tool_call")
    tr = next(e for e in trace if e.type == "tool_result")
    assert tc.node_id == "cp"
    assert tc.data == {"name": "edit_file", "args": {"path": "a.txt"}}
    assert tr.data == {"name": "edit_file", "success": True}


# ─── Working directory ───────────────────────────────────────────────────────

def test_scratch_workdir_per_run(fake_runtime, monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.setattr("app.config.get_settings",
                        lambda: SimpleNamespace(data_dir=tmp_path))
    fake_runtime.result = _result()
    run_workflow_sync(_wf(), {}, thread_id="run-8")

    expected = str(tmp_path / "runs" / "run-8" / "copilot-cp")
    assert fake_runtime.calls[0]["working_dir"] == expected
    assert os.path.isdir(expected)


def test_explicit_workdir_passed_through(fake_runtime, tmp_path):
    wd = str(tmp_path / "my-wd")
    fake_runtime.result = _result()
    run_workflow_sync(_wf(working_dir=wd), {}, thread_id="run-9")
    assert fake_runtime.calls[0]["working_dir"] == wd
    assert os.path.isdir(wd)


# ─── Auth ────────────────────────────────────────────────────────────────────

def test_auth_ref_resolves_secret(fake_runtime, monkeypatch):
    monkeypatch.setattr("app.secrets.get_secret", lambda name: "gh-token-123")
    fake_runtime.result = _result()
    run_workflow_sync(_wf(auth_ref="gh_token"), {}, thread_id="run-10")
    assert fake_runtime.calls[0]["github_token"] == "gh-token-123"


def test_auth_ref_missing_secret_fails(fake_runtime, monkeypatch):
    monkeypatch.setattr("app.secrets.get_secret", lambda name: None)
    with pytest.raises(Exception, match="secret 'gh_token' not found"):
        run_workflow_sync(_wf(auth_ref="gh_token"), {}, thread_id="run-11")


# ─── Validation ──────────────────────────────────────────────────────────────

def test_validation_empty_task():
    r = validate_workflow(_wf(task="   "))
    assert "E_COPILOT_TASK_EMPTY" in [i.code for i in r.issues]


def test_validation_relative_workdir():
    r = validate_workflow(_wf(working_dir="relative/path"))
    assert "E_COPILOT_WORKDIR_RELATIVE" in [i.code for i in r.issues]


# ─── Permission policies (unit, SDK installed in dev env) ───────────────────

def _run_handler(handler, request):
    return asyncio.run(handler(request, None))


def test_safe_only_denies_shell(tmp_path):
    from app.engine.copilot.permissions import build_permission_handler
    from copilot.rpc import PermissionDecisionReject
    from copilot.session_events import PermissionRequestShell

    handler = build_permission_handler("safe_only", str(tmp_path))
    req = PermissionRequestShell(
        can_offer_session_approval=False, commands=[], full_command_text="rm -rf /",
        has_write_file_redirection=False, intention="test",
        possible_paths=[], possible_urls=[],
    )
    assert isinstance(_run_handler(handler, req), PermissionDecisionReject)


def test_safe_only_denies_url(tmp_path):
    from app.engine.copilot.permissions import build_permission_handler
    from copilot.rpc import PermissionDecisionReject
    from copilot.session_events import PermissionRequestUrl

    handler = build_permission_handler("safe_only", str(tmp_path))
    req = PermissionRequestUrl(url="https://example.com", intention="test")
    assert isinstance(_run_handler(handler, req), PermissionDecisionReject)


def test_safe_only_write_inside_allowed_outside_denied(tmp_path):
    from app.engine.copilot.permissions import build_permission_handler
    from copilot.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject
    from copilot.session_events import PermissionRequestWrite

    handler = build_permission_handler("safe_only", str(tmp_path))

    inside = PermissionRequestWrite(
        can_offer_session_approval=False, diff="", file_name=str(tmp_path / "a.txt"),
        intention="test",
    )
    assert isinstance(_run_handler(handler, inside), PermissionDecisionApproveOnce)

    outside = PermissionRequestWrite(
        can_offer_session_approval=False, diff="", file_name="/etc/passwd",
        intention="test",
    )
    assert isinstance(_run_handler(handler, outside), PermissionDecisionReject)


def test_approve_all_returns_sdk_handler(tmp_path):
    from app.engine.copilot.permissions import build_permission_handler
    from copilot.session import PermissionHandler

    assert build_permission_handler("approve_all", str(tmp_path)) is PermissionHandler.approve_all


def test_runtime_env_ambient_returns_none():
    from app.engine.copilot.runtime import _runtime_env

    assert _runtime_env(None) is None
    assert _runtime_env("") is None


def test_runtime_env_injects_git_credentials_for_token():
    from app.engine.copilot.runtime import _runtime_env

    env = _runtime_env("ghp_test123")
    assert env is not None
    assert env["GITHUB_TOKEN"] == "ghp_test123"
    assert env["GH_TOKEN"] == "ghp_test123"
    # git credential helper via GIT_CONFIG_* — github.com-scoped, token served
    # from the process env (never written to a config file).
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "credential.https://github.com.helper"
    assert "$GITHUB_TOKEN" in env["GIT_CONFIG_VALUE_0"]
    # Inherits the surrounding environment (SDK replaces, not merges).
    assert env.get("PATH") == os.environ.get("PATH")


def test_ambient_gh_token_absent_returns_none(monkeypatch):
    from app.engine.copilot import runtime as rt
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert asyncio.run(rt._ambient_gh_token()) is None


def test_ambient_gh_token_reads_fake_gh(tmp_path, monkeypatch):
    from app.engine.copilot import runtime as rt
    fake = tmp_path / "gh"
    fake.write_text("#!/bin/sh\necho gho_fake123\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    assert asyncio.run(rt._ambient_gh_token()) == "gho_fake123"
