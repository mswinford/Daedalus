"""Agent node handler: LLM provider + tool-calling loop."""
import json
from typing import Any

from schema.models import Node, AgentNodeConfig, ToolDefinition
from app.engine.llm import LLMProvider, Message
from app.engine.tools import build_tool_schema, execute_tool
from app.engine.nodes.base import AgentState, NodeContext, _render_template


class AgentExecutor:
    def __init__(self, node_id: str, config: AgentNodeConfig, provider: LLMProvider, ctx: NodeContext):
        self.node_id = node_id
        self.config = config
        self.provider = provider
        self.ctx = ctx

        base_prompt = config.system_prompt
        if config.prompt_ref:
            prompt_def = next((p for p in ctx.workflow.prompts if p.id == config.prompt_ref), None)
            if not prompt_def:
                raise ValueError(f"Prompt {config.prompt_ref} not found")
            base_prompt = prompt_def.text

        skill_prompts = [s.prompt for s in config.skills]
        tool_ids = list(dict.fromkeys(
            list(config.tool_ids) + [tid for s in config.skills for tid in s.tool_ids]
        ))

        self._tools_by_name: dict[str, ToolDefinition] = {}
        self._tool_schemas: list[dict[str, Any]] = []
        for tid in tool_ids:
            tool_def = next((t for t in ctx.workflow.tools if t.id == tid), None)
            if tool_def:
                self._tools_by_name[tool_def.name] = tool_def
                self._tool_schemas.append(build_tool_schema(tool_def))

        self._base_prompt = base_prompt
        self._skill_prompts = skill_prompts

    async def run(self, state: AgentState) -> AgentState:
        config = self.config
        provider = self.provider
        node_id = self.node_id
        tools_by_name = self._tools_by_name
        tool_schemas = self._tool_schemas
        base_prompt = self._base_prompt
        skill_prompts = self._skill_prompts

        messages = list(state.get("messages_by_node", {}).get(node_id, []))
        system_prompt = _render_template(base_prompt, state)
        for skill_prompt in skill_prompts:
            system_prompt += "\n\n" + _render_template(skill_prompt, state)
        llm_messages = [Message(role="system", content=system_prompt)]
        llm_messages.extend(messages)

        if not any(getattr(m, "role", None) in ("user", "assistant") for m in llm_messages):
            data = state.get("data", {})
            user_content = json.dumps(data, ensure_ascii=False) if data else "Begin."
            llm_messages.append(Message(role="user", content=user_content))

        tools = tool_schemas if tool_schemas else None
        final_content = ""

        for _ in range(config.max_iterations):
            result = await provider.chat(
                messages=llm_messages,
                tools=tools,
                temperature=config.temperature,
            )
            self.ctx._record_llm_call(node_id, config.model_id, result)
            final_content = result.content

            if not result.tool_calls:
                llm_messages.append(Message(role="assistant", content=result.content))
                break

            llm_messages.append(Message(
                role="assistant", content=result.content, tool_calls=result.tool_calls,
            ))

            for tc in result.tool_calls:
                func_name = tc.get("function", {}).get("name", "")
                try:
                    args = json.loads(tc.get("function", {}).get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_def = tools_by_name.get(func_name)
                if tool_def:
                    tool_result = await execute_tool(tool_def, args, dict(state))
                else:
                    tool_result = json.dumps({"error": f"Unknown tool: {func_name}"})
                llm_messages.append(Message(
                    role="tool", content=tool_result, tool_call_id=tc.get("id", ""),
                ))

        new_messages = list(messages)
        new_messages.append(Message(role="assistant", content=final_content))

        return {
            "messages_by_node": {
                **state.get("messages_by_node", {}),
                node_id: new_messages,
            },
            "output": final_content,
            "_node_outputs": {
                **state.get("_node_outputs", {}),
                node_id: {"content": final_content},
            },
        }


class AgentHandler:
    def build(self, node: Node, ctx: NodeContext):
        config: AgentNodeConfig = node.config
        provider = ctx.providers.get(config.model_id)
        if not provider:
            raise ValueError(f"Model {config.model_id} not found")
        executor = AgentExecutor(node.id, config, provider, ctx)
        return executor.run
