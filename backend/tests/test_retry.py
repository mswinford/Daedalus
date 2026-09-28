"""Tests for per-node retry: error classifier + the instrumented retry loop."""
import asyncio

import pytest

from app.engine.builder import GraphBuilder
from app.engine.retry import classify_error
from app.engine.runner import run_workflow_sync
from schema.models import Edge, Node, RetryConfig, Workflow


# ── classifier ───────────────────────────────────────────────────────────────

def test_classify_timeout_types():
    assert classify_error(TimeoutError()) == "timeout"
    assert classify_error(asyncio.TimeoutError()) == "timeout"


def test_classify_structured_status_codes():
    class HttpErr(Exception):
        def __init__(self, status: int):
            super().__init__("request failed")
            self.status_code = status

    assert classify_error(HttpErr(429)) == "rate_limit"
    assert classify_error(HttpErr(503)) == "server_error"
    assert classify_error(HttpErr(404)) is None


def test_classify_messages():
    assert classify_error(Exception("Request timed out after 30s")) == "timeout"
    assert classify_error(Exception("Error code: 503 - server overloaded")) == "server_error"
    assert classify_error(Exception("HTTP 429 Too Many Requests")) == "rate_limit"
    assert classify_error(Exception("rate limit exceeded, retry later")) == "rate_limit"


def test_classify_not_retryable():
    assert classify_error(ValueError("boom")) is None
    # A bare number in prose is not a status code.
    assert classify_error(Exception("row 503 failed validation")) is None
    assert classify_error(KeyError("missing")) is None


# ── instrumented retry loop ──────────────────────────────────────────────────

def _builder(trace: list) -> GraphBuilder:
    wf = Workflow(id="wf", name="wf",
                  nodes=[Node(id="start", type="start", config={})], edges=[])
    return GraphBuilder(wf, trace=trace)


def test_succeeds_after_retries():
    trace: list = []
    calls = 0

    async def flaky(state):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("slow")
        return {"data": {"ok": True}}

    wrapped = _builder(trace)._instrument(
        "n", flaky, retry=RetryConfig(enabled=True, max_retries=3, backoff_base=0.001))
    out = asyncio.run(wrapped({}))

    assert out["data"] == {"ok": True}
    assert calls == 3
    retries = [e for e in trace if e.type == "retry"]
    assert [r.data["attempt"] for r in retries] == [1, 2]
    assert all(r.data["category"] == "timeout" for r in retries)
    assert any(e.type == "node_end" for e in trace)


def test_exhaustion_raises_after_full_budget():
    trace: list = []
    calls = 0

    async def always_fails(state):
        nonlocal calls
        calls += 1
        raise TimeoutError("slow")

    wrapped = _builder(trace)._instrument(
        "n", always_fails, retry=RetryConfig(enabled=True, max_retries=2, backoff_base=0.001))
    with pytest.raises(TimeoutError):
        asyncio.run(wrapped({}))

    assert calls == 3  # initial + 2 retries
    assert trace[-1].type == "node_error"


def test_non_retryable_fails_fast():
    trace: list = []
    calls = 0

    async def bad(state):
        nonlocal calls
        calls += 1
        raise ValueError("logic error")

    wrapped = _builder(trace)._instrument(
        "n", bad, retry=RetryConfig(enabled=True, max_retries=3, backoff_base=0.001))
    with pytest.raises(ValueError):
        asyncio.run(wrapped({}))

    assert calls == 1
    assert not [e for e in trace if e.type == "retry"]


def test_category_not_in_retry_on_fails_fast():
    trace: list = []
    calls = 0

    async def slow(state):
        nonlocal calls
        calls += 1
        raise TimeoutError("slow")

    wrapped = _builder(trace)._instrument(
        "n", slow, retry=RetryConfig(enabled=True, max_retries=3, backoff_base=0.001,
                                     retry_on=["rate_limit"]))
    with pytest.raises(TimeoutError):
        asyncio.run(wrapped({}))

    assert calls == 1


def test_disabled_config_never_retries():
    trace: list = []
    calls = 0

    async def slow(state):
        nonlocal calls
        calls += 1
        raise TimeoutError("slow")

    wrapped = _builder(trace)._instrument(
        "n", slow, retry=RetryConfig(enabled=False, max_retries=3))
    with pytest.raises(TimeoutError):
        asyncio.run(wrapped({}))

    assert calls == 1


def test_graph_interrupt_never_retried():
    from langgraph.errors import GraphInterrupt
    from langgraph.types import Interrupt

    trace: list = []
    calls = 0

    async def pause(state):
        nonlocal calls
        calls += 1
        raise GraphInterrupt([Interrupt(value="pause")])

    wrapped = _builder(trace)._instrument(
        "n", pause, retry=RetryConfig(enabled=True, max_retries=3, backoff_base=0.001))
    with pytest.raises(GraphInterrupt):
        asyncio.run(wrapped({}))

    assert calls == 1


def test_exhaustion_with_catch_error_returns_marker():
    trace: list = []

    async def slow(state):
        raise TimeoutError("slow")

    wrapped = _builder(trace)._instrument(
        "n", slow, catch_error=True,
        retry=RetryConfig(enabled=True, max_retries=1, backoff_base=0.001))
    out = asyncio.run(wrapped({}))

    assert "_error_info" in out
    assert out["_error_info"]["node_id"] == "n"


# ── end-to-end through a real graph ──────────────────────────────────────────

def test_retry_e2e_custom_function():
    wf = Workflow(
        id="wf-retry", name="retry",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="cf", type="custom_function",
                 config={"code": 'raise TimeoutError("t")',
                         "retry": {"enabled": True, "max_retries": 2, "backoff_base": 0.001}}),
            Node(id="end", type="end", config={}),
        ],
        edges=[
            Edge(id="a", source_node_id="start", source_handle="default", target_node_id="cf"),
            Edge(id="b", source_node_id="cf", source_handle="default", target_node_id="end"),
        ],
    )
    trace: list = []
    with pytest.raises(Exception, match="t"):
        run_workflow_sync(wf, {}, trace=trace)

    retries = [e for e in trace if e.type == "retry"]
    assert len(retries) == 2
    assert all(e.node_id == "cf" for e in retries)
