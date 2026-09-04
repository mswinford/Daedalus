"""Translate workflow JSON into a LangGraph StateGraph."""
import asyncio
import json
import time
from typing import Any, Callable
from langgraph.errors import GraphInterrupt
from langgraph.graph import StateGraph, START, END

from schema.models import (
    Workflow, Node, Edge, ConditionalNodeConfig, RunEvent, RetryConfig,
)
from app.engine.llm import create_provider, LLMProvider
from app.engine.conditions import evaluate_condition, ConditionError
from app.engine.retry import classify_error
from app.secrets import get_secret
from app.engine.nodes import HANDLERS
from app.engine.nodes.base import AgentState


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


class GraphBuilder:
    """Translates a workflow definition into a compiled LangGraph graph."""

    def __init__(
        self,
        workflow: Workflow,
        trace: list[RunEvent] | None = None,
        on_event: Callable[[RunEvent], None] | None = None,
        invocations: dict[str, Any] | None = None,
    ):
        self.workflow = workflow
        self.graph = StateGraph(AgentState)
        self.providers: dict[str, LLMProvider] = {}
        self._nodes_by_id = {n.id: n for n in workflow.nodes}
        # invoke node id → InvocationInfo from build-time expansion (empty when
        # the workflow has no invoke nodes).
        self.invocations = invocations or {}
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

    def _instrument(self, node_id: str, func: Callable, catch_error: bool = False,
                    retry: RetryConfig | None = None) -> Callable:
        """Wrap a node function to emit node_start/node_end (or node_error) with timing.

        When `catch_error` is set (the node owns a type='error' edge), an
        exception is converted into an `_error_info` marker instead of
        propagating, so the node's router can send the run down its error
        edge. A successful run clears the marker. GraphInterrupt always
        propagates — it signals a pause, not a failure.

        When `retry` is enabled, transient failures (classified by
        classify_error and listed in retry_on) are re-invoked with
        exponential backoff before the failure path runs. Node functions
        return new state without mutating it, so each attempt starts clean.
        """
        rc = retry if retry and retry.enabled else None
        max_attempts = (rc.max_retries + 1) if rc else 1

        async def wrapped(state: AgentState) -> AgentState:
            started = time.perf_counter()
            self._emit(RunEvent(type="node_start", node_id=node_id, timestamp=time.time()))
            attempt = 0
            while True:
                try:
                    result = await func(state)
                    break
                except GraphInterrupt:
                    raise
                except Exception as exc:
                    attempt += 1
                    category = classify_error(exc) if rc else None
                    if category is None or category not in rc.retry_on or attempt >= max_attempts:
                        duration_ms = (time.perf_counter() - started) * 1000
                        self._emit(RunEvent(
                            type="node_error", node_id=node_id, timestamp=time.time(),
                            data={"error": str(exc), "duration_ms": round(duration_ms, 2)},
                        ))
                        if not catch_error:
                            raise
                        return {"_error_info": {"node_id": node_id, "error": str(exc)}}
                    delay = min(rc.backoff_base * (2 ** (attempt - 1)), 30.0)
                    self._emit(RunEvent(
                        type="retry", node_id=node_id, timestamp=time.time(),
                        data={
                            "attempt": attempt,
                            "max_retries": rc.max_retries,
                            "error": str(exc),
                            "category": category,
                            "delay_s": delay,
                        },
                    ))
                    await asyncio.sleep(delay)
            duration_ms = (time.perf_counter() - started) * 1000
            output = result.get("_node_outputs", {}).get(node_id) if isinstance(result, dict) else None
            self._emit(RunEvent(
                type="node_end", node_id=node_id, timestamp=time.time(),
                data={"duration_ms": round(duration_ms, 2), "output": _summarize(output)},
            ))
            if isinstance(result, dict):
                # A node may deliberately set a non-empty marker on success
                # (the invoke exit gate re-keys a region failure to its own id
                # so its router can take the parent's error edge); only clear
                # markers the node didn't set.
                marker = result.get("_error_info")
                return {**result, "_error_info": marker if marker else {}}
            return result
        return wrapped

    def _build_providers(self):
        """Create LLM providers from workflow model configs.

        ``api_key_ref`` holds a *secret name*, never a key value: the value is
        resolved from the secrets store (env var first, then file). A set ref
        that does not resolve fails loudly at build time rather than silently
        treating the name as a literal key.
        """
        for model_config in self.workflow.models:
            ref = model_config.api_key_ref
            api_key = None
            if ref:
                api_key = get_secret(ref)
                if api_key is None:
                    raise ValueError(
                        f"Model '{model_config.name}' references secret "
                        f"'{ref}', but it is not set. Add it in the Secrets "
                        f"panel or export it as an environment variable."
                    )
            self.providers[model_config.id] = create_provider({
                "provider": model_config.provider.value,
                "model": model_config.model,
                "base_url": model_config.base_url,
                "api_key": api_key,
            })

    def _get_node_func(self, node: Node) -> Callable:
        """Get the LangGraph node function for a node type."""
        handler = HANDLERS.get(node.type)
        if handler is None:
            raise ValueError(f"Unknown node type: {node.type}")
        return handler.build(node, self)

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
        is_conditional = node.type == "conditional"

        error_edge = next((e for e in edges if e.type == "error"), None)

        if is_conditional:
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
                node.id,
                self._instrument(
                    node.id, node_func,
                    catch_error=node.id in error_sources,
                    retry=getattr(node.config, "retry", None),
                ),
            )

        # Add edges
        self._build_edges()

        # Compile
        return self.graph.compile(checkpointer=checkpointer)
