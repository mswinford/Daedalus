"""Tool schema building and execution for agent tool-calling."""
import asyncio
import base64
import json
import os
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


# ─── GitHub builtins ──────────────────────────────────────────────────────────
#
# Token is never a tool argument: it resolves via get_secret("GITHUB_TOKEN")
# (env var first, then the secrets file), so the key never enters state,
# checkpoints, or the LLM's context. Base URL defaults to github.com; set
# GITHUB_BASE_URL for GitHub Enterprise Server.

_GITHUB_SECRET = "GITHUB_TOKEN"


def _github_client(token: str) -> Any:
    """Async client for the GitHub API. A factory so tests can swap in a mock transport."""
    import httpx
    return httpx.AsyncClient(
        base_url=os.environ.get("GITHUB_BASE_URL", "https://api.github.com"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30.0,
    )


def _gh_error(resp: Any) -> dict[str, Any]:
    """Curated error for a failed GitHub API response (no raw dumps)."""
    detail = ""
    try:
        body = resp.json()
        if isinstance(body, dict):
            detail = str(body.get("message") or "")
            errs = body.get("errors")
            if isinstance(errs, list):
                msgs = [str(e.get("message")) for e in errs if isinstance(e, dict) and e.get("message")]
                if msgs:
                    detail = "; ".join(msgs)
    except Exception:
        pass
    msg = f"GitHub API {resp.status_code}" + (f": {detail}" if detail else "")
    headers = getattr(resp, "headers", {}) or {}
    if resp.status_code in (401, 403):
        if "rate limit" in detail.lower() or headers.get("x-ratelimit-remaining") == "0":
            msg += f" (rate limited; resets at unix {headers.get('ratelimit-reset', '?')})"
        else:
            msg += f" — check that {_GITHUB_SECRET} is set and has repo scope"
    return {"error": msg}


def _gh_precheck(arguments: dict, required: tuple[str, ...]) -> dict | None:
    """Missing-argument / missing-secret guard shared by the github_* builtins."""
    missing = [n for n in required if not arguments.get(n)]
    if missing:
        return {"error": f"missing required arguments: {', '.join(missing)}"}
    if not get_secret(_GITHUB_SECRET):
        return {
            "error": (
                f"{_GITHUB_SECRET} secret is not configured — add it via the Secrets panel "
                f"or set the {_GITHUB_SECRET} environment variable"
            )
        }
    return None


def _quote_path(path: str) -> str:
    from urllib.parse import quote
    return "/".join(quote(p, safe="") for p in path.split("/") if p)


@register_builtin("github_create_branch")
async def _builtin_github_create_branch(arguments: dict, state: dict) -> Any:
    guard = _gh_precheck(arguments, ("owner", "repo", "branch"))
    if guard:
        return guard
    owner, repo, branch = arguments["owner"], arguments["repo"], arguments["branch"]
    base = arguments.get("base") or None
    async with _github_client(get_secret(_GITHUB_SECRET)) as client:  # type: ignore[arg-type]
        if not base:
            r = await client.get(f"/repos/{owner}/{repo}")
            if r.status_code != 200:
                return _gh_error(r)
            base = r.json().get("default_branch") or "main"
        r = await client.get(f"/repos/{owner}/{repo}/commits/{base}")
        if r.status_code != 200:
            return _gh_error(r)
        sha = r.json().get("sha", "")
        r = await client.post(
            f"/repos/{owner}/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        if r.status_code not in (200, 201):
            return _gh_error(r)
    return {
        "branch": branch,
        "sha": sha,
        "url": f"https://github.com/{owner}/{repo}/tree/{branch}",
    }


@register_builtin("github_write_file")
async def _builtin_github_write_file(arguments: dict, state: dict) -> Any:
    guard = _gh_precheck(arguments, ("owner", "repo", "path", "content", "message", "branch"))
    if guard:
        return guard
    owner, repo, branch = arguments["owner"], arguments["repo"], arguments["branch"]
    path = _quote_path(str(arguments["path"]))
    content_b64 = base64.b64encode(str(arguments["content"]).encode("utf-8")).decode("ascii")
    async with _github_client(get_secret(_GITHUB_SECRET)) as client:  # type: ignore[arg-type]
        existing_sha = None
        r = await client.get(f"/repos/{owner}/{repo}/contents/{path}", params={"ref": branch})
        if r.status_code == 200:
            existing_sha = r.json().get("sha")
        elif r.status_code != 404:
            return _gh_error(r)
        payload: dict[str, Any] = {
            "message": arguments["message"],
            "content": content_b64,
            "branch": branch,
        }
        if existing_sha:
            payload["sha"] = existing_sha  # GitHub requires the current sha to update a file
        r = await client.put(f"/repos/{owner}/{repo}/contents/{path}", json=payload)
        if r.status_code not in (200, 201):
            return _gh_error(r)
        commit = r.json().get("commit") or {}
    return {
        "path": arguments["path"],
        "sha": commit.get("sha"),
        "commit_url": commit.get("html_url"),
    }


@register_builtin("github_read_file")
async def _builtin_github_read_file(arguments: dict, state: dict) -> Any:
    guard = _gh_precheck(arguments, ("owner", "repo", "path"))
    if guard:
        return guard
    owner, repo = arguments["owner"], arguments["repo"]
    ref = arguments.get("ref") or None
    path = _quote_path(str(arguments["path"]))
    async with _github_client(get_secret(_GITHUB_SECRET)) as client:  # type: ignore[arg-type]
        if not ref:
            r = await client.get(f"/repos/{owner}/{repo}")
            if r.status_code != 200:
                return _gh_error(r)
            ref = r.json().get("default_branch") or "main"
        r = await client.get(
            f"/repos/{owner}/{repo}/contents/{path}",
            params={"ref": ref},
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        if r.status_code == 404:
            return {"error": f"file not found: {arguments['path']} on {ref}"}
        if r.status_code != 200:
            return _gh_error(r)
    return {"path": arguments["path"], "ref": ref, "content": r.text}


@register_builtin("github_create_pr")
async def _builtin_github_create_pr(arguments: dict, state: dict) -> Any:
    guard = _gh_precheck(arguments, ("owner", "repo", "title", "head"))
    if guard:
        return guard
    owner, repo = arguments["owner"], arguments["repo"]
    base = arguments.get("base") or None
    payload: dict[str, Any] = {
        "title": arguments["title"],
        "head": arguments["head"],
    }
    if arguments.get("body"):
        payload["body"] = arguments["body"]
    async with _github_client(get_secret(_GITHUB_SECRET)) as client:  # type: ignore[arg-type]
        if not base:
            r = await client.get(f"/repos/{owner}/{repo}")
            if r.status_code != 200:
                return _gh_error(r)
            base = r.json().get("default_branch") or "main"
        payload["base"] = base
        r = await client.post(f"/repos/{owner}/{repo}/pulls", json=payload)
        if r.status_code not in (200, 201):
            return _gh_error(r)
        pr = r.json()
    return {
        "number": pr.get("number"),
        "url": pr.get("html_url"),
        "state": pr.get("state"),
    }


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
