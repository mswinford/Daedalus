"""Translate workflow JSON into a LangGraph StateGraph."""
import asyncio
from typing import Any, Callable
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from schema.models import (
    Workflow, Node, Edge, AgentNodeConfig,
    StartNodeConfig, EndNodeConfig, ConditionalNodeConfig,
    TransformNodeConfig, CustomFunctionNodeConfig, HumanInLoopNodeConfig,
)
from app.engine.llm import create_provider, LLMProvider
from app.engine.conditions import evaluate_condition, ConditionError
from app.sandbox.runner import run_sandboxed


# LangGraph state type
class AgentState(TypedDict):
    messages: list[Any]
    output: str
    error: str
    data: dict[str, Any]
    _node_outputs: dict[str, Any]


class GraphBuilder:
    """Translates a workflow definition into a compiled LangGraph graph."""

    def __init__(self, workflow: Workflow):
        self.workflow = workflow
        self.graph = StateGraph(AgentState)
        self.providers: dict[str, LLMProvider] = {}
        self._build_providers()

    def _build_providers(self):
        """Create LLM providers from workflow model configs."""
        for model_config in self.workflow.models:
            self.providers[model_config.id] = create_provider({
                "provider": model_config.provider.value,
                "model": model_config.model,
                "base_url": model_config.base_url,
                "api_key": model_config.api_key_ref,
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
            "messages": [],
            "output": "",
            "error": "",
            "data": {},
            "_node_outputs": {},
        }

    async def _end_node(self, state: AgentState) -> AgentState:
        """End node: finalizes the workflow."""
        return state

    def _agent_node(self, node: Node) -> Callable:
        """Create an agent node function."""
        config: AgentNodeConfig = node.config
        provider = self.providers.get(config.model_id)
        if not provider:
            raise ValueError(f"Model {config.model_id} not found")

        async def agent_func(state: AgentState) -> AgentState:
            messages = state.get("messages", [])
            system_prompt = config.system_prompt

            # Build messages for the LLM call
            from app.engine.llm import Message
            llm_messages = [
                Message(role="system", content=system_prompt),
            ]
            llm_messages.extend(messages)

            result = await provider.chat(
                messages=llm_messages,
                temperature=config.temperature,
            )

            # Append assistant message
            new_messages = list(messages)
            new_messages.append(Message(
                role="assistant",
                content=result.content,
            ))

            return {
                "messages": new_messages,
                "output": result.content,
                "_node_outputs": {
                    **state.get("_node_outputs", {}),
                    node.id: {"content": result.content, "tokens": result.tokens_output},
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
            output = ""
            if config.mode == "template" and config.template:
                # Simple template substitution
                output = config.template
                for field_name, field_value in state.items():
                    if isinstance(field_value, str):
                        output = output.replace(f"{{{{{field_name}}}}}", field_value)
            elif config.mode == "mapping" and config.field_mappings:
                result = {}
                for mapping in config.field_mappings:
                    result[mapping.target] = state.get(mapping.source, "")
                output = str(result)

            return {
                "output": output,
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

            return {
                "output": str(result),
                "_node_outputs": {
                    **state.get("_node_outputs", {}),
                    node.id: result,
                },
            }

        return custom_func

    def _human_in_loop_node(self, node: Node) -> Callable:
        """Human-in-loop node: pauses for input."""
        async def human_func(state: AgentState) -> AgentState:
            raise NotImplementedError("Human-in-loop coming in Phase 3")
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
        """Build (path_func, path_map) routing a node's outgoing edges by condition."""
        is_conditional_node = node.type == "conditional"

        if is_conditional_node:
            config: ConditionalNodeConfig = node.config
            conditions = config.conditions
            branches = [e for e in edges if e.source_handle != "default"]
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

    def build(self) -> Any:
        """Build and return the compiled LangGraph graph."""
        # Add nodes
        for node in self.workflow.nodes:
            if node.type in ("start", "end"):
                continue  # Start and end are handled by edges
            node_func = self._get_node_func(node)
            self.graph.add_node(node.id, node_func)

        # Add edges
        self._build_edges()

        # Compile
        return self.graph.compile()
