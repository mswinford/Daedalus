"""Tool schema building and execution for agent tool-calling."""
import asyncio
import json
import re
from typing import Any, Callable, Coroutine

from schema.models import ToolDefinition, ToolImplementationType, StateFieldType
from app.sandbox.runner import run_sandboxed
from app.secrets import get_secret

_ARG_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
_ENV_PLACEHOLDER_RE = re.compile(r"\$\{(\w+)\}")


def _render_template(template: str, values: dict[str, Any]) -> tuple[str, list[str]]:
    """Render a template string.

    ``${NAME}`` placeholders are filled from the process environment (for secrets such as
    API tokens); ``{name}`` placeholders are filled from ``values`` (the tool arguments).
    Returns ``(rendered, missing)`` where ``missing`` lists names that could not be resolved
    (env vars left blank, argument placeholders left as-is).
    """
    missing: list[str] = []

    def env_repl(m: re.Match[str]) -> str:
        var = m.group(1)
        val = get_secret(var)
        if val is not None:
            return val
        missing.append(var)
        return ""

    rendered = _ENV_PLACEHOLDER_RE.sub(env_repl, template)

    def arg_repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if values.get(key) is not None:
            return str(values[key])
        missing.append(key)
        return m.group(0)

    rendered = _ARG_PLACEHOLDER_RE.sub(arg_repl, rendered)
    return rendered, missing


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
        url_template = str(impl.config.get("url") or "")
        method = str(impl.config.get("method", "GET")).upper()
        try:
            timeout = float(impl.config.get("timeout_seconds", 30))
        except (TypeError, ValueError):
            timeout = 30.0
        headers_cfg = impl.config.get("headers") or {}

        # Render the URL from arguments (e.g. https://api.github.com/repos/{owner}/{repo}).
        url, missing_url = _render_template(url_template, arguments)
        if missing_url:
            return json.dumps({
                "error": f"HTTP tool '{tool.name}' is missing values for: {', '.join(missing_url)}"
            })

        # Arguments already consumed by the URL are not re-sent in the query/body.
        consumed = set(_ARG_PLACEHOLDER_RE.findall(url_template))
        remaining = {k: v for k, v in arguments.items() if k not in consumed}

        # Render headers from arguments + environment (e.g. Authorization: Bearer ${GITHUB_TOKEN}).
        headers: dict[str, str] = {}
        missing_headers: list[str] = []
        for hname, htmpl in headers_cfg.items():
            val, miss = _render_template(str(htmpl), arguments)
            headers[str(hname)] = val
            missing_headers.extend(miss)
        if missing_headers:
            return json.dumps({
                "error": f"HTTP tool '{tool.name}' is missing values for header placeholders: {', '.join(missing_headers)}"
            })

        try:
            import httpx
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method == "GET":
                    resp = await client.get(url, params=remaining or None, headers=headers or None)
                else:
                    resp = await client.request(method, url, json=remaining or None, headers=headers or None)
                return json.dumps({"status": resp.status_code, "body": resp.text})
        except Exception as e:
            return json.dumps({"error": f"HTTP request failed: {e}"})

    return json.dumps({"error": f"Unknown implementation type: {impl.type.value}"})
