"""Translate workflow JSON into a LangGraph StateGraph."""
import asyncio
import json
import re
import time
from typing import Any, Callable
from langgraph.errors import GraphInterrupt
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from typing_extensions import TypedDict

from schema.models import (
    Workflow, Node, Edge, AgentNodeConfig,
    StartNodeConfig, EndNodeConfig, ConditionalNodeConfig,
    TransformNodeConfig, CustomFunctionNodeConfig, HumanInLoopNodeConfig,
    ToolDefinition, RunEvent,
)
from app.engine.llm import create_provider, LLMProvider, Message
from app.engine.conditions import evaluate_condition, ConditionError, _resolve_path
from app.engine.tools import build_tool_schema, execute_tool
from app.sandbox.runner import run_sandboxed
from app.secrets import get_secret


_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def _render_template(template: str, state: dict) -> str:
    """Replace {{path}} placeholders with values resolved from state.

    Paths are dot-separated and may be nested (e.g. data.score or
    _node_outputs.grade.label). Missing paths render as an empty string.
    """
    def _sub(match):
        value = _resolve_path(state, match.group(1))
        return "" if value is None else str(value)
    return _TEMPLATE_VAR_RE.sub(_sub, template)


def _summarize(value: Any, limit: int = 500) -> Any:
    """Compact, JSON-safe view of a node's output for the debug log.

    Long strings are truncated so a single verbose output doesn't flood the trace.
    """
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) > limit:
        return text[:limit] + f"… ({len(text) - limit} more chars)"
    return value


# LangGraph state type
class AgentState(TypedDict):
    messages_by_node: dict[str, list[Any]]
    output: str
    error: str
    data: dict[str, Any]
    _node_outputs: dict[str, Any]
    # Set by the instrument wrapper when a node with an error edge fails;
    # cleared on every successful node run so stale markers can't misroute.
    _error_info: dict[str, Any]


class GraphBuilder:
    """Translates a workflow definition into a compiled LangGraph graph."""

    def __init__(
        self,
        workflow: Workflow,
        trace: list[RunEvent] | None = None,
        on_event: Callable[[RunEvent], None] | None = None,
    ):
        self.workflow = workflow
        self.graph = StateGraph(AgentState)
        self.providers: dict[str, LLMProvider] = {}
        self._nodes_by_id = {n.id: n for n in workflow.nodes}
        # Execution trace (node_start/node_end/llm_call events). Callers may pass
        # a shared list to collect events even when a run fails mid-graph.
        self._trace: list[RunEvent] = trace if trace is not None else []
        # Optional live hook invoked for every event as it happens (used by the
        # WebSocket streamer). The trace list still collects everything regardless.
        self._on_event = on_event
        self._token_usage: dict[str, dict[str, int]] = {}
        self._build_providers()

    def _emit(self, event: RunEvent) -> None:
        """Record an event in the trace and forward it to the live hook, if any."""
        self._trace.append(event)
        if self._on_event is not None:
            self._on_event(event)

    # ─── Execution tracing ────────────────────────────────────────────────

    @property
    def total_tokens_input(self) -> int:
        return sum(v["input"] for v in self._token_usage.values())

    @property
    def total_tokens_output(self) -> int:
        return sum(v["output"] for v in self._token_usage.values())

    @property
    def estimated_cost_usd(self) -> float:
        """Sum per-model token usage against each model's pricing (per 1M tokens)."""
        cost = 0.0
        for model_id, usage in self._token_usage.items():
            mc = next((m for m in self.workflow.models if m.id == model_id), None)
            if not mc or not mc.pricing:
                continue
            price_in = mc.pricing.get("input", 0.0)
            price_out = mc.pricing.get("output", 0.0)
            cost += usage["input"] / 1_000_000 * price_in + usage["output"] / 1_000_000 * price_out
        return round(cost, 6)

    def _record_llm_call(self, node_id: str, model_id: str, result: "LLMResult") -> None:
        """Accumulate token usage and emit an llm_call trace event."""
        bucket = self._token_usage.setdefault(model_id, {"input": 0, "output": 0})
        bucket["input"] += result.tokens_input
        bucket["output"] += result.tokens_output
        self._emit(RunEvent(
            type="llm_call", node_id=node_id, timestamp=time.time(),
            data={
                "model": model_id,
                "tokens_input": result.tokens_input,
                "tokens_output": result.tokens_output,
                "tool_calls": [tc.get("function", {}).get("name") for tc in result.tool_calls],
            },
        ))

    def _instrument(self, node_id: str, func: Callable, catch_error: bool = False) -> Callable:
        """Wrap a node function to emit node_start/node_end (or node_error) with timing.

        When `catch_error` is set (the node owns a type='error' edge), an
        exception is converted into an `_error_info` marker instead of
        propagating, so the node's router can send the run down its error
        edge. A successful run clears the marker. GraphInterrupt always
        propagates — it signals a pause, not a failure.
        """
        async def wrapped(state: AgentState) -> AgentState:
            started = time.perf_counter()
            self._emit(RunEvent(type="node_start", node_id=node_id, timestamp=time.time()))
            try:
                result = await func(state)
            except GraphInterrupt:
                raise
            except Exception as exc:
                duration_ms = (time.perf_counter() - started) * 1000
                self._emit(RunEvent(
                    type="node_error", node_id=node_id, timestamp=time.time(),
                    data={"error": str(exc), "duration_ms": round(duration_ms, 2)},
                ))
                if not catch_error:
                    raise
                return {"_error_info": {"node_id": node_id, "error": str(exc)}}
            duration_ms = (time.perf_counter() - started) * 1000
            output = result.get("_node_outputs", {}).get(node_id) if isinstance(result, dict) else None
            self._emit(RunEvent(
                type="node_end", node_id=node_id, timestamp=time.time(),
                data={"duration_ms": round(duration_ms, 2), "output": _summarize(output)},
            ))
            if isinstance(result, dict):
                return {**result, "_error_info": {}}
            return result
        return wrapped

    def _build_providers(self):
        """Create LLM providers from workflow model configs."""
        for model_config in self.workflow.models:
            api_key = model_config.api_key_ref
            if api_key:
                resolved = get_secret(api_key)
                if resolved is not None:
                    api_key = resolved
            self.providers[model_config.id] = create_provider({
                "provider": model_config.provider.value,
                "model": model_config.model,
                "base_url": model_config.base_url,
                "api_key": api_key,
            })

    def _get_node_func(self, node: Node) -> Callable:
        """Get the LangGraph node function for a node type."""
        if node.type == "start":
            return self._start_node
        elif node.type == "end":
            return self._end_node
        elif node.type == "agent":
            return self._agent_node(node)
        elif node.type == "conditional":
            return self._conditional_node(node)
        elif node.type == "transform":
            return self._transform_node(node)
        elif node.type == "custom_function":
            return self._custom_function_node(node)
        elif node.type == "human_in_loop":
            return self._human_in_loop_node(node)
        else:
            raise ValueError(f"Unknown node type: {node.type}")

    async def _start_node(self, state: AgentState) -> AgentState:
        """Start node: initializes the workflow."""
        return {
            "messages_by_node": {},
            "output": "",
            "error": "",
            "data": {},
            "_node_outputs": {},
            "_error_info": {},
        }

    async def _end_node(self, state: AgentState) -> AgentState:
        """End node: finalizes the workflow."""
        return state

    def _agent_node(self, node: Node) -> Callable:
        """Create an agent node function with tool-calling loop."""
        config: AgentNodeConfig = node.config
        provider = self.providers.get(config.model_id)
        if not provider:
            raise ValueError(f"Model {config.model_id} not found")

        base_prompt = config.system_prompt
        if config.prompt_ref:
            prompt_def = next((p for p in self.workflow.prompts if p.id == config.prompt_ref), None)
            if not prompt_def:
                raise ValueError(f"Prompt {config.prompt_ref} not found")
            base_prompt = prompt_def.text

        skill_prompts = [s.prompt for s in config.skills]
        tool_ids = list(dict.fromkeys(
            list(config.tool_ids) + [tid for s in config.skills for tid in s.tool_ids]
        ))

        tools_by_name: dict[str, ToolDefinition] = {}
        tool_schemas: list[dict[str, Any]] = []
        for tid in tool_ids:
            tool_def = next((t for t in self.workflow.tools if t.id == tid), None)
            if tool_def:
                tools_by_name[tool_def.name] = tool_def
                tool_schemas.append(build_tool_schema(tool_def))

        async def agent_func(state: AgentState) -> AgentState:
            messages = list(state.get("messages_by_node", {}).get(node.id, []))
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
                self._record_llm_call(node.id, config.model_id, result)
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
                    node.id: new_messages,
                },
                "output": final_content,
                "_node_outputs": {
                    **state.get("_node_outputs", {}),
                    node.id: {"content": final_content},
                },
            }

        return agent_func

    def _conditional_node(self, node: Node) -> Callable:
        """Conditional node: routes based on state."""
        async def conditional_func(state: AgentState) -> AgentState:
            return state
        return conditional_func

    def _transform_node(self, node: Node) -> Callable:
        """Transform node: transforms data."""
        config: TransformNodeConfig = node.config

        async def transform_func(state: AgentState) -> AgentState:
            if config.mode == "custom_function" and config.custom_function_id:
                ref = self._nodes_by_id.get(config.custom_function_id)
                if not ref or ref.type != "custom_function":
                    raise ValueError(
                        f"Transform '{node.id}' references unknown or non-custom_function "
                        f"node '{config.custom_function_id}'"
                    )
                ref_config: CustomFunctionNodeConfig = ref.config
                result = await asyncio.to_thread(
                    run_sandboxed, ref_config.code, dict(state), ref_config.timeout_seconds
                )
                if "error" in result:
                    raise RuntimeError(
                        f"Transform '{node.id}' custom function failed: {result['error']}"
                    )

                new_data = {**state.get("data", {}), config.output_field: result}
                return {
                    "output": str(result),
                    "data": new_data,
                    "_node_outputs": {
                        **state.get("_node_outputs", {}),
                        node.id: result,
                    },
                }

            output = ""
            if config.mode == "template" and config.template:
                output = _render_template(config.template, state)
            elif config.mode == "mapping" and config.field_mappings:
                result = {}
                for mapping in config.field_mappings:
                    value = _resolve_path(state, mapping.source)
                    result[mapping.target] = "" if value is None else value
                output = str(result)

            new_data = {**state.get("data", {}), config.output_field: output}

            return {
                "output": output,
                "data": new_data,
                "_node_outputs": {
                    **state.get("_node_outputs", {}),
                    node.id: {config.output_field: output},
                },
            }

        return transform_func

    def _custom_function_node(self, node: Node) -> Callable:
        """Custom function node: executes user Python code."""
        config: CustomFunctionNodeConfig = node.config

        async def custom_func(state: AgentState) -> AgentState:
            result = await asyncio.to_thread(
                run_sandboxed, config.code, dict(state), config.timeout_seconds
            )

            if "error" in result:
                raise RuntimeError(f"Custom function '{node.id}' failed: {result['error']}")

            # Write back the declared output fields into `data` so downstream
            # nodes can address them (e.g. $.data.grade). Undeclared keys stay
            # only in _node_outputs.
            new_data = {**state.get("data", {})}
            for field in config.output_fields:
                if field in result:
                    new_data[field] = result[field]

            return {
                "output": str(result),
                "data": new_data,
                "_node_outputs": {
                    **state.get("_node_outputs", {}),
                    node.id: result,
                },
            }

        return custom_func

    def _human_in_loop_node(self, node: Node) -> Callable:
        """Human-in-loop node: pauses execution until a human provides input."""
        config: HumanInLoopNodeConfig = node.config

        async def human_func(state: AgentState) -> AgentState:
            payload = {
                "node_id": node.id,
                # Carried so a restarted process can rebuild the run record
                # from the checkpoint alone (see recover_paused_runs).
                "workflow_id": self.workflow.id,
                "message": config.approval_message or "Please provide input",
                "fields": [f.model_dump() for f in config.input_fields],
                "approval_required": config.approval_required,
                "timeout_seconds": config.timeout_seconds,
                "requested_at": time.time(),
            }
            response = interrupt(payload)

            # Rejection: if approval is required and the human explicitly rejected, fail the run.
            if config.approval_required and isinstance(response, dict) and response.get("approved") is False:
                raise RuntimeError(f"Human rejected at node '{node.id}'")

            new_data = {**state.get("data", {})}
            output_fields = config.output_fields or []
            if isinstance(response, dict):
                if len(output_fields) == 1:
                    key = output_fields[0]
                    if key in response:
                        new_data[key] = response[key]
                    elif len(config.input_fields) == 1:
                        new_data[key] = response.get(config.input_fields[0].name)
                    else:
                        new_data[key] = response
                elif output_fields:
                    for i, key in enumerate(output_fields):
                        if key in response:
                            new_data[key] = response[key]
                        elif i < len(config.input_fields):
                            new_data[key] = response.get(config.input_fields[i].name)
                else:
                    new_data.update(response)
            elif output_fields:
                new_data[output_fields[0]] = response

            return {
                "output": str(response),
                "data": new_data,
                "_node_outputs": {
                    **state.get("_node_outputs", {}),
                    node.id: {"response": response},
                },
            }
        return human_func

    def _find_default_edge(self, edges: list, preferred_handle: str | None,
                           allow_static_fallback: bool):
        """Pick the fallback edge: preferred handle, else 'default', else (optionally) first static."""
        if preferred_handle:
            for e in edges:
                if e.source_handle == preferred_handle:
                    return e
        for e in edges:
            if e.source_handle == "default":
                return e
        if allow_static_fallback:
            for e in edges:
                if e.type == "static":
                    return e
        return None

    def _make_router(self, node: Node, edges: list) -> tuple[Callable, dict]:
        """Build (path_func, path_map) routing a node's outgoing edges by condition.

        type='error' edges are not conditions — they're only taken when this
        node just failed (the `_error_info` marker), which is checked first.
        """
        is_conditional_node = node.type == "conditional"

        error_edge = next((e for e in edges if e.type == "error"), None)

        if is_conditional_node:
            config: ConditionalNodeConfig = node.config
            conditions = config.conditions
            branches = [e for e in edges if e.source_handle != "default" and e.type != "error"]
            default_edge = self._find_default_edge(edges, config.default_branch, False)
        else:
            conditional_edges = [e for e in edges if e.type == "conditional" and e.condition]
            conditions = [e.condition for e in conditional_edges]
            branches = conditional_edges
            default_edge = self._find_default_edge(edges, None, True)

        nodes_by_id = {n.id: n for n in self.workflow.nodes}
        path_map: dict[str, str] = {}
        for e in edges:
            target_node = nodes_by_id.get(e.target_node_id)
            dst = END if (target_node and target_node.type == "end") else e.target_node_id
            path_map[e.source_handle] = dst

        def router(state: AgentState) -> str:
            marker = state.get("_error_info") or {}
            if error_edge is not None and marker.get("node_id") == node.id:
                return error_edge.source_handle
            for i, cond in enumerate(conditions):
                if evaluate_condition(cond, state) and i < len(branches):
                    return branches[i].source_handle
            if default_edge is not None:
                return default_edge.source_handle
            raise ConditionError(
                f"No condition matched for node '{node.id}' and no default branch"
            )

        return router, path_map

    def _build_edges(self):
        """Add edges to the graph, wiring conditional sources via add_conditional_edges."""
        nodes_by_id = {n.id: n for n in self.workflow.nodes}

        # Group outgoing edges by source node id, preserving workflow order.
        out_edges: dict[str, list] = {}
        for edge in self.workflow.edges:
            out_edges.setdefault(edge.source_node_id, []).append(edge)

        for node in self.workflow.nodes:
            edges = out_edges.get(node.id, [])
            if not edges:
                continue

            src = START if node.type == "start" else node.id

            has_conditional = (
                node.type == "conditional"
                or any(e.type == "conditional" and e.condition for e in edges)
                or any(e.type == "error" for e in edges)
            )

            if has_conditional and node.type != "start":
                router, path_map = self._make_router(node, edges)
                self.graph.add_conditional_edges(src, router, path_map)
            else:
                for edge in edges:
                    target_node = nodes_by_id.get(edge.target_node_id)
                    if not target_node:
                        continue
                    dst = END if target_node.type == "end" else edge.target_node_id
                    self.graph.add_edge(src, dst)

        # Fallback entry point when the workflow has no start node
        if not any(n.type == "start" for n in self.workflow.nodes):
            for node in self.workflow.nodes:
                if node.type != "end":
                    self.graph.add_edge(START, node.id)
                    break

    def build(self, checkpointer: Any = None) -> Any:
        """Build and return the compiled LangGraph graph."""
        # Nodes owning a type='error' edge catch their own exceptions so the
        # router can send the run down that edge instead of failing the run.
        error_sources = {e.source_node_id for e in self.workflow.edges if e.type == "error"}

        # Add nodes
        for node in self.workflow.nodes:
            if node.type in ("start", "end"):
                continue  # Start and end are handled by edges
            node_func = self._get_node_func(node)
            self.graph.add_node(
                node.id, self._instrument(node.id, node_func, catch_error=node.id in error_sources)
            )

        # Add edges
        self._build_edges()

        # Compile
        return self.graph.compile(checkpointer=checkpointer)
