"""Tests for per-node execution tracing: timing, ordering, and LLM token capture."""
import asyncio

from app.engine.builder import GraphBuilder
from app.engine.llm import LLMProvider, LLMResult
from app.engine.runner import run_workflow_sync
from schema.models import (
    Workflow, Node, Edge, ModelConfig, AgentNodeConfig,
)


def _simple_wf():
    """start -> custom_function -> transform -> end (no LLM)."""
    return Workflow(
        id="wf", name="wf",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="cf", type="custom_function",
                 config={"code": 'result["grade"] = "A"', "output_fields": ["grade"]}),
            Node(id="tf", type="transform",
                 config={"mode": "template", "template": "x", "output_field": "out"}),
            Node(id="end", type="end", config={}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="cf"),
            Edge(id="c", source_node_id="cf", source_handle="default", target_node_id="tf"),
            Edge(id="t", source_node_id="tf", source_handle="default", target_node_id="end"),
        ],
    )


def test_run_emits_node_trace_in_order():
    out = run_workflow_sync(_simple_wf(), {})

    events = out["events"]
    # Each executed node produces a node_start immediately followed by a node_end.
    starts = [ev.node_id for ev in events if ev.type == "node_start"]
    ends = [ev.node_id for ev in events if ev.type == "node_end"]
    assert starts == ["cf", "tf"]
    assert ends == ["cf", "tf"]

    # cf runs before tf, and each node's node_start precedes its own node_end.
    seq = [(ev.type, ev.node_id) for ev in events]
    assert seq.index(("node_start", "cf")) < seq.index(("node_start", "tf"))
    for i, (t, nid) in enumerate(seq):
        if t == "node_end":
            start_idx = next(j for j in range(i) if seq[j] == ("node_start", nid))
            assert start_idx < i

    # Every node_end carries a non-negative duration and a summarized output.
    for ev in events:
        if ev.type == "node_end":
            assert ev.data["duration_ms"] >= 0
            assert "output" in ev.data


def test_run_reports_zero_tokens_for_non_llm_workflow():
    out = run_workflow_sync(_simple_wf(), {})
    assert out["total_tokens_input"] == 0
    assert out["total_tokens_output"] == 0
    assert out["estimated_cost_usd"] == 0.0


class _FakeProvider(LLMProvider):
    def __init__(self, result: LLMResult):
        self._result = result
        self.calls = 0

    async def chat(self, messages, tools=None, temperature=None, max_tokens=None):
        self.calls += 1
        return self._result

    async def chat_stream(self, *a, **k):
        raise NotImplementedError


def _agent_wf():
    return Workflow(
        id="wf-agent", name="agent-wf",
        models=[ModelConfig(id="m1", name="M", provider="openai_compatible", model="x")],
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="agent", type="agent",
                 config=AgentNodeConfig(model_id="m1", system_prompt="hi")),
            Node(id="end", type="end", config={}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="agent"),
            Edge(id="e", source_node_id="agent", source_handle="default", target_node_id="end"),
        ],
    )


def test_agent_token_usage_and_llm_call_event():
    builder = GraphBuilder(_agent_wf())
    fake = _FakeProvider(LLMResult(content="hello", tokens_input=10, tokens_output=5))
    builder.providers["m1"] = fake
    graph = builder.build()

    asyncio.run(graph.ainvoke({
        "messages": [], "output": "", "error": "", "data": {}, "_node_outputs": {},
    }))

    assert fake.calls == 1
    assert builder.total_tokens_input == 10
    assert builder.total_tokens_output == 5

    llm_events = [ev for ev in builder._trace if ev.type == "llm_call"]
    assert len(llm_events) == 1
    assert llm_events[0].node_id == "agent"
    assert llm_events[0].data["tokens_input"] == 10
    assert llm_events[0].data["tokens_output"] == 5


def test_agent_cost_uses_model_pricing():
    wf = _agent_wf()
    wf.models[0].pricing = {"input": 3.0, "output": 15.0}  # $/1M tokens
    builder = GraphBuilder(wf)
    fake = _FakeProvider(LLMResult(content="hello", tokens_input=1_000_000, tokens_output=2_000_000))
    builder.providers["m1"] = fake
    graph = builder.build()

    asyncio.run(graph.ainvoke({
        "messages": [], "output": "", "error": "", "data": {}, "_node_outputs": {},
    }))

    # 1M input * $3/1M + 2M output * $15/1M = $3 + $30 = $33
    assert builder.estimated_cost_usd == 33.0
