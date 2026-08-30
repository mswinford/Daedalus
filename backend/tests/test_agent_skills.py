"""Tests for agent prompt_ref + skills[] runtime fold-in (R1.7)."""
import asyncio
import json

from app.engine.builder import GraphBuilder
from schema.models import (
    AgentSkill,
    Edge,
    JsonSchemaParam,
    ModelConfig,
    Node,
    PromptDefinition,
    StateFieldType,
    ToolDefinition,
    ToolImplementation,
    ToolImplementationType,
    Workflow,
)


class SkillToolProvider:
    """Returns a tool call for `triple` (a skill-provided tool), then a final answer."""

    def __init__(self):
        self.calls = []

    async def chat(self, messages, tools=None, temperature=None, max_tokens=None):
        from app.engine.llm import LLMResult
        self.calls.append({"messages": messages, "tools": tools})
        if len(self.calls) == 1:
            return LLMResult(
                content="",
                tool_calls=[{
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "triple", "arguments": json.dumps({"n": 7})},
                }],
            )
        return LLMResult(content="done: 21", tool_calls=[])

    async def chat_stream(self, messages, tools=None, temperature=None, max_tokens=None):
        yield "done: 21"


def _triple_tool() -> ToolDefinition:
    return ToolDefinition(
        id="triple_tool", name="triple",
        description="Triple a number",
        parameters={"n": JsonSchemaParam(type=StateFieldType.NUMBER, required=True)},
        implementation=ToolImplementation(
            type=ToolImplementationType.CUSTOM_FUNCTION,
            config={"code": 'result["value"] = state["arguments"]["n"] * 3'},
        ),
    )


def _wf(agent_config: dict, prompts=None, tools=None) -> Workflow:
    return Workflow(
        id="wf", name="skill-test",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="agent1", type="agent", config=agent_config),
            Node(id="end", type="end", config={}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="agent1"),
            Edge(id="a", source_node_id="agent1", source_handle="default", target_node_id="end"),
        ],
        models=[ModelConfig(id="m1", name="Mock", provider="openai_compatible", model="mock")],
        prompts=prompts or [],
        tools=tools if tools is not None else [_triple_tool()],
    )


def _system_message(provider) -> str:
    return provider.calls[0]["messages"][0].content


def _invoke(graph, data=None):
    return asyncio.run(graph.ainvoke({
        "messages_by_node": {}, "output": "", "error": "",
        "data": data or {}, "_node_outputs": {},
    }))


def test_skill_fold_in_prompt_and_tools():
    """Skill prompt appends to the system prompt; skill tools join the agent's tool set."""
    wf = _wf({
        "model_id": "m1",
        "system_prompt": "Base instructions.",
        "tool_ids": [],
        "skills": [AgentSkill(
            name="math-extra",
            prompt="Use the triple tool for x3.",
            tool_ids=["triple_tool"],
        )],
    })
    provider = SkillToolProvider()
    builder = GraphBuilder(wf)
    builder.providers["m1"] = provider
    graph = builder.build()

    result = _invoke(graph)

    assert _system_message(provider) == "Base instructions.\n\nUse the triple tool for x3."
    tool_names = [t["function"]["name"] for t in (provider.calls[0]["tools"] or [])]
    assert tool_names == ["triple"]
    # The skill-provided tool is actually callable by the agent loop
    tool_msgs = [m for m in provider.calls[1]["messages"] if m.role == "tool"]
    assert json.loads(tool_msgs[0].content) == {"value": 21}
    assert result["output"] == "done: 21"


def test_prompt_ref_resolves_template_from_data():
    """prompt_ref selects the workflow prompt template; {{vars}} resolve from state."""
    wf = _wf(
        {
            "model_id": "m1",
            "system_prompt": "FALLBACK (must not be used)",
            "tool_ids": [],
            "prompt_ref": "greeting",
        },
        prompts=[PromptDefinition(id="greeting", name="Greeting",
                                  text="Greet {{data.name}} politely.")],
    )
    provider = SkillToolProvider()
    builder = GraphBuilder(wf)
    builder.providers["m1"] = provider
    graph = builder.build()

    _invoke(graph, data={"name": "Ada"})

    assert _system_message(provider) == "Greet Ada politely."


def test_prompt_ref_with_skills_combined():
    """prompt_ref template is the base; skill prompts append after it."""
    wf = _wf(
        {
            "model_id": "m1",
            "system_prompt": "unused",
            "tool_ids": [],
            "prompt_ref": "base",
            "skills": [AgentSkill(prompt="Extra skill rules.", tool_ids=[])],
        },
        prompts=[PromptDefinition(id="base", text="You are {{data.role}}.")],
    )
    provider = SkillToolProvider()
    builder = GraphBuilder(wf)
    builder.providers["m1"] = provider
    graph = builder.build()

    _invoke(graph, data={"role": "a reviewer"})

    assert _system_message(provider) == "You are a reviewer.\n\nExtra skill rules."


def test_agent_unknown_prompt_ref_raises():
    wf = _wf({"model_id": "m1", "system_prompt": "x", "prompt_ref": "ghost"})
    builder = GraphBuilder(wf)
    builder.providers["m1"] = SkillToolProvider()
    try:
        builder.build()
        raise AssertionError("expected ValueError for unknown prompt_ref")
    except ValueError as e:
        assert "Prompt ghost not found" in str(e)


def test_agent_without_prompt_ref_uses_system_prompt():
    wf = _wf({"model_id": "m1", "system_prompt": "Plain prompt.", "tool_ids": []})
    provider = SkillToolProvider()
    builder = GraphBuilder(wf)
    builder.providers["m1"] = provider
    graph = builder.build()

    _invoke(graph)

    assert _system_message(provider) == "Plain prompt."


def test_legacy_workflow_without_prompts_field_parses():
    """Old workflow JSON without a prompts[] key still loads (default empty list)."""
    raw = {
        "id": "wf", "name": "legacy",
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "agent1", "type": "agent",
             "config": {"model_id": "m1", "system_prompt": "hi"}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "s", "source_node_id": "start", "source_handle": "default", "target_node_id": "agent1"},
            {"id": "a", "source_node_id": "agent1", "source_handle": "default", "target_node_id": "end"},
        ],
        "models": [{"id": "m1", "name": "Mock", "provider": "openai_compatible", "model": "mock"}],
    }
    wf = Workflow.model_validate(raw)
    assert wf.prompts == []
    agent_cfg = wf.nodes[1].config
    assert agent_cfg.prompt_ref is None
    assert agent_cfg.skills == []
