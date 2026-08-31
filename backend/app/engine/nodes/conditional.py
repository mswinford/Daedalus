"""Conditional node handler: routes based on state (routing done by edges)."""
from schema.models import Node
from app.engine.nodes.base import AgentState, NodeContext


class ConditionalHandler:
    def build(self, node: Node, ctx: NodeContext):
        async def conditional_func(state: AgentState) -> AgentState:
            return state
        return conditional_func
