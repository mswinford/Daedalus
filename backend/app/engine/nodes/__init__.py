"""Node-type handler registry: node.type -> NodeHandler instance."""
from app.engine.nodes.base import AgentState, NodeContext, NodeHandler
from app.engine.nodes.agent import AgentHandler
from app.engine.nodes.conditional import ConditionalHandler
from app.engine.nodes.transform import TransformHandler
from app.engine.nodes.custom_function import CustomFunctionHandler
from app.engine.nodes.human_in_loop import HumanInLoopHandler

HANDLERS: dict[str, NodeHandler] = {
    "agent": AgentHandler(),
    "conditional": ConditionalHandler(),
    "transform": TransformHandler(),
    "custom_function": CustomFunctionHandler(),
    "human_in_loop": HumanInLoopHandler(),
}

__all__ = ["HANDLERS", "AgentState", "NodeContext", "NodeHandler"]
