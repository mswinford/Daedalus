"""Unit tests for AgentExecutor (R4: agent tool-calling closure extracted to a class)."""
import asyncio
import json

from app.engine.builder import GraphBuilder
from app.engine.llm import LLMResult
from app.engine.nodes.agent import AgentExecutor
from schema.models import (
    Workflow, Node, Edge, ModelConfig, AgentNodeConfig,
)


class FakeProvider:
    """Plain object returning queued LLMResults from chat()."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def chat(self, messages, tools=None, temperature=None, max_tokens=None):
        self.calls.append({"messages": messages, "tools": tools})
        return self.results.pop(0)


def _wf(agent_config, prompts=None, tools=None) -> Workflow:
    return Workflow(
        id="wf", name="executor-test",
        models=[ModelConfig(id="m1", name="Mock", provider="openai_compatible", model="mock")],
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="agent1", type="agent", config=agent_config),
            Node(id="end", type="end", config={}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="agent1"),
            Edge(id="a", source_node_id="agent1", source_handle="default", target_node_id="end"),
        ],
        prompts=prompts or [],
        tools=tools or [],
    )


def _make_executor(agent_config, provider):
    wf = _wf(agent_config)
    builder = GraphBuilder(wf)
    builder.providers["m1"] = provider
    node = next(n for n in wf.nodes if n.type == "agent")
    return AgentExecutor(node.id, node.config, provider, builder)


def test_no_tool_calls_single_chat():
    """No tool calls: one chat call, final content returned, assistant message appended."""
    provider = FakeProvider([LLMResult(content="final answer", tokens_input=3, tokens_output=2)])
    ex = _make_executor(AgentNodeConfig(model_id="m1", system_prompt="Be brief."), provider)

    out = asyncio.run(ex.run({
        "messages_by_node": {}, "output": "", "error": "",
        "data": {"q": 1}, "_node_outputs": {},
    }))

    assert len(provider.calls) == 1
    assert out["output"] == "final answer"
    msgs = out["messages_by_node"]["agent1"]
    assert len(msgs) == 1
    assert msgs[0].role == "assistant" and msgs[0].content == "final answer"
    assert out["_node_outputs"]["agent1"] == {"content": "final answer"}


def test_unknown_tool_call_round():
    """First response calls an unknown tool: error JSON appended as tool message, loop runs twice."""
    provider = FakeProvider([
        LLMResult(content="", tool_calls=[{
            "id": "c1", "type": "function",
            "function": {"name": "nope", "arguments": "{}"},
        }]),
        LLMResult(content="all done"),
    ])
    ex = _make_executor(AgentNodeConfig(model_id="m1", system_prompt="s"), provider)

    out = asyncio.run(ex.run({
        "messages_by_node": {}, "output": "", "error": "", "data": {}, "_node_outputs": {},
    }))

    assert len(provider.calls) == 2
    tool_msgs = [m for m in provider.calls[1]["messages"] if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert json.loads(tool_msgs[0].content) == {"error": "Unknown tool: nope"}
    assert out["output"] == "all done"


def test_missing_prompt_ref_raises_at_construction():
    """prompt_ref to a non-existent prompt raises ValueError when the executor is built."""
    provider = FakeProvider([])
    try:
        _make_executor(AgentNodeConfig(model_id="m1", system_prompt="x", prompt_ref="ghost"), provider)
    except ValueError as e:
        assert "ghost" in str(e)
    else:
        raise AssertionError("expected ValueError for missing prompt_ref")
