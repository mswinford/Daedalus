"""Tool schema building and execution for agent tool-calling."""
import asyncio
import json
from typing import Any, Callable, Coroutine

from schema.models import ToolDefinition, ToolImplementationType, StateFieldType
from app.sandbox.runner import run_sandboxed


def build_tool_schema(tool: ToolDefinition) -> dict[str, Any]:
    """Convert a ToolDefinition to OpenAI tool-calling format."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in tool.parameters.items():
        prop: dict[str, Any] = {"type": param.type.value}
        if param.description:
            prop["description"] = param.description
        if param.enum:
            prop["enum"] = param.enum
        properties[name] = prop
        if param.required:
            required.append(name)

    params_schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        params_schema["required"] = required

    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": params_schema,
        },
    }


_BUILTINS: dict[str, Callable[..., Coroutine[Any, Any, Any]]] = {}


def register_builtin(name: str):
    """Decorator to register a builtin tool handler."""
    def wrapper(fn):
        _BUILTINS[name] = fn
        return fn
    return wrapper


@register_builtin("echo")
async def _builtin_echo(arguments: dict, state: dict) -> Any:
    return arguments.get("message", "")


async def execute_tool(
    tool: ToolDefinition, arguments: dict[str, Any], state: dict
) -> str:
    """Execute a tool call and return the result as a string."""
    impl = tool.implementation

    if impl.type == ToolImplementationType.CUSTOM_FUNCTION:
        code = impl.config.get("code", "")
        sandbox_state = {**state, "arguments": arguments}
        timeout = impl.config.get("timeout_seconds", 30)
        result = await asyncio.to_thread(run_sandboxed, code, sandbox_state, timeout)
        if "error" in result:
            return json.dumps({"error": result["error"]})
        return json.dumps(result)

    elif impl.type == ToolImplementationType.BUILTIN:
        func_name = impl.config.get("function", "")
        handler = _BUILTINS.get(func_name)
        if not handler:
            return json.dumps({"error": f"Unknown builtin function: {func_name}"})
        try:
            result = await handler(arguments, state)
            return result if isinstance(result, str) else json.dumps(result)
        except Exception as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}"})

    elif impl.type == ToolImplementationType.HTTP:
        url = impl.config.get("url", "")
        method = impl.config.get("method", "GET").upper()
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30) as client:
                if method == "GET":
                    resp = await client.get(url, params=arguments)
                else:
                    resp = await client.request(method, url, json=arguments)
                return json.dumps({"status": resp.status_code, "body": resp.text})
        except Exception as e:
            return json.dumps({"error": f"HTTP request failed: {e}"})

    return json.dumps({"error": f"Unknown implementation type: {impl.type.value}"})
