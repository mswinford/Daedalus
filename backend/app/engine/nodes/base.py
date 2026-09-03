"""Shared types for node-type handlers: state, context and handler protocols."""
import re
from typing import Any, Awaitable, Callable, Protocol

from typing_extensions import TypedDict

from schema.models import Node
from app.engine.conditions import _resolve_path


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
    # Parent frames stashed by invoke entry gates (invoke_id → stashed channels).
    _invoke_stash: dict[str, dict[str, Any]]


class NodeContext(Protocol):
    """Structural interface handlers use to reach back into the GraphBuilder."""

    workflow: Any
    providers: dict[str, Any]
    _nodes_by_id: dict[str, Node]
    invocations: dict[str, Any]  # invoke node id → InvocationInfo (from expansion)
    run_id: str | None  # set by the runner; used for per-run scratch paths

    def _emit(self, event: Any) -> None: ...

    def _record_llm_call(self, node_id: str, model_id: str, result: Any) -> None: ...


class NodeHandler(Protocol):
    """One method per handler: return the LangGraph node function for a node."""

    def build(self, node: Node, ctx: NodeContext) -> Callable[[AgentState], Awaitable[AgentState]]: ...
