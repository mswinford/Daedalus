"""Tests for the github_* builtin tools (mocked httpx transport, no network)."""
import asyncio
import json

import httpx
import pytest

from app.engine.tools import execute_tool
from schema.models import (
    ToolDefinition, ToolImplementation, ToolImplementationType,
)


class FakeGitHub:
    """Minimal in-memory GitHub API covering the routes the builtins use."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.default_branch = "main"
        self.head_sha = "a" * 40
        self.refs: set[str] = set()
        self.files: dict[tuple[str, str, str, str], str] = {}  # (owner, repo, branch, path) -> sha
        self.contents: dict[tuple[str, str, str, str], str] = {}  # same key -> raw content
        self.pulls: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        method, path = request.method, request.url.path
        parts = [p for p in path.split("/") if p]  # ['repos', owner, repo, ...]
        assert parts[0] == "repos"
        owner, repo = parts[1], parts[2]
        rest = parts[3:]

        if method == "GET" and not rest:
            return httpx.Response(200, json={"default_branch": self.default_branch})

        if method == "GET" and rest[:1] == ["commits"]:
            return httpx.Response(200, json={"sha": self.head_sha})

        if method == "POST" and rest[:1] == ["git"] and rest[1:2] == ["refs"]:
            body = json.loads(request.content)
            ref = body["ref"].removeprefix("refs/heads/")
            if ref in self.refs:
                return httpx.Response(422, json={"message": "Reference already exists"})
            self.refs.add(ref)
            return httpx.Response(201, json={"ref": f"refs/heads/{ref}", "object": {"sha": body["sha"]}})

        if rest[:1] == ["contents"]:
            file_path = "/".join(rest[1:])
            key = (owner, repo, request.url.params.get("ref", ""), file_path)
            if method == "GET":
                if key in self.files or key in self.contents:
                    if "raw" in request.headers.get("accept", ""):
                        return httpx.Response(200, text=self.contents.get(key, ""))
                    return httpx.Response(200, json={"sha": self.files.get(key, "x")})
                return httpx.Response(404, json={"message": "Not Found"})
            # PUT — create or update
            body = json.loads(request.content)
            if key in self.files and body.get("sha") != self.files[key]:
                return httpx.Response(409, json={"message": "sha does not match"})
            new_sha = f"{len(self.files):040x}"
            self.files[key] = new_sha
            return httpx.Response(
                200, json={"commit": {"sha": new_sha, "html_url": f"https://github.com/{owner}/{repo}/commit/{new_sha}"}}
            )

        if method == "POST" and rest[:1] == ["pulls"]:
            body = json.loads(request.content)
            pr = {
                "number": len(self.pulls) + 1,
                "html_url": f"https://github.com/{owner}/{repo}/pull/{len(self.pulls) + 1}",
                "state": "open",
                **body,
            }
            self.pulls.append(pr)
            return httpx.Response(201, json=pr)

        return httpx.Response(404, json={"message": f"unrouted {method} {path}"})


def make_tool(fn_name: str) -> ToolDefinition:
    return ToolDefinition(
        id="t", name=fn_name, description="", parameters={},
        implementation=ToolImplementation(type=ToolImplementationType.BUILTIN, config={"function": fn_name}),
    )


def install(monkeypatch, fake: FakeGitHub, token: str = "test-token") -> dict:
    """Point the builtins at a mock transport; mirrors _github_client's auth header."""
    seen: dict[str, str | None] = {"token": None}

    def factory(tok):
        seen["token"] = tok
        return httpx.AsyncClient(
            transport=httpx.MockTransport(fake.handler),
            base_url="https://api.github.com",
            headers={"Authorization": f"Bearer {tok}"},
        )

    monkeypatch.setattr("app.engine.tools.get_secret", lambda name: token)
    monkeypatch.setattr("app.engine.tools._github_client", factory)
    return seen


def run_builtin(monkeypatch, fake: FakeGitHub, fn_name: str, args: dict) -> tuple[dict, dict]:
    seen = install(monkeypatch, fake)
    result = asyncio.run(execute_tool(make_tool(fn_name), args, {}))
    return json.loads(result), seen


ARGS = {"owner": "acme", "repo": "widget"}


# --- github_create_branch ---

def test_create_branch_defaults_to_default_branch(monkeypatch):
    fake = FakeGitHub()
    out, seen = run_builtin(monkeypatch, fake, "github_create_branch", {**ARGS, "branch": "feat/x"})
    assert out == {"branch": "feat/x", "sha": fake.head_sha,
                   "url": "https://github.com/acme/widget/tree/feat/x"}
    paths = [r.url.path for r in fake.requests]
    assert paths == ["/repos/acme/widget", "/repos/acme/widget/commits/main", "/repos/acme/widget/git/refs"]
    assert all(r.headers["authorization"] == "Bearer test-token" for r in fake.requests)
    assert seen["token"] == "test-token"  # get_secret result feeds the client


def test_create_branch_explicit_base_skips_repo_lookup(monkeypatch):
    fake = FakeGitHub()
    out, _ = run_builtin(monkeypatch, fake, "github_create_branch", {**ARGS, "branch": "b", "base": "develop"})
    assert out["branch"] == "b"
    paths = [r.url.path for r in fake.requests]
    assert "/repos/acme/widget/commits/develop" in paths
    assert "/repos/acme/widget" not in paths


def test_create_branch_existing_ref_fails_loud(monkeypatch):
    fake = FakeGitHub()
    fake.refs.add("feat/x")
    out, seen = run_builtin(monkeypatch, fake, "github_create_branch", {**ARGS, "branch": "feat/x"})
    assert "422" in out["error"] and "already exists" in out["error"]


# --- github_write_file ---

def test_write_file_creates_new_file(monkeypatch):
    fake = FakeGitHub()
    out, _ = run_builtin(monkeypatch, fake, "github_write_file", {
        **ARGS, "path": "docs/README.md", "content": "# hi", "message": "add readme", "branch": "feat/x",
    })
    assert out["path"] == "docs/README.md"
    assert out["commit_url"].startswith("https://github.com/acme/widget/commit/")
    put = [r for r in fake.requests if r.method == "PUT"][0]
    body = json.loads(put.content)
    assert "sha" not in body  # new file — no existing sha sent
    import base64
    assert base64.b64decode(body["content"]).decode() == "# hi"


def test_write_file_update_sends_existing_sha(monkeypatch):
    fake = FakeGitHub()
    fake.files[("acme", "widget", "feat/x", "app.py")] = "oldsha"
    out, _ = run_builtin(monkeypatch, fake, "github_write_file", {
        **ARGS, "path": "app.py", "content": "print(1)", "message": "update", "branch": "feat/x",
    })
    assert "error" not in out
    put = [r for r in fake.requests if r.method == "PUT"][0]
    assert json.loads(put.content)["sha"] == "oldsha"


def test_write_file_requires_branch(monkeypatch):
    fake = FakeGitHub()
    out, _ = run_builtin(monkeypatch, fake, "github_write_file", {
        **ARGS, "path": "a.txt", "content": "x", "message": "m",
    })
    assert "branch" in out["error"]
    assert fake.requests == []  # no HTTP at all


# --- github_read_file ---

def test_read_file_returns_raw_content_from_default_branch(monkeypatch):
    fake = FakeGitHub()
    fake.files[("acme", "widget", "main", "app.py")] = "sha1"
    fake.contents[("acme", "widget", "main", "app.py")] = "print('hello')\n"
    out, _ = run_builtin(monkeypatch, fake, "github_read_file", {**ARGS, "path": "app.py"})
    assert out == {"path": "app.py", "ref": "main", "content": "print('hello')\n"}
    get = [r for r in fake.requests if r.url.path.endswith("/contents/app.py")][0]
    assert "raw" in get.headers["accept"]  # raw media type, not the JSON contents API


def test_read_file_explicit_ref(monkeypatch):
    fake = FakeGitHub()
    fake.contents[("acme", "widget", "v1.0", "README.md")] = "# v1"
    out, _ = run_builtin(monkeypatch, fake, "github_read_file", {**ARGS, "path": "README.md", "ref": "v1.0"})
    assert out["content"] == "# v1" and out["ref"] == "v1.0"
    assert "/repos/acme/widget" not in [r.url.path for r in fake.requests]  # no default-branch lookup


def test_read_file_not_found(monkeypatch):
    fake = FakeGitHub()
    out, _ = run_builtin(monkeypatch, fake, "github_read_file", {**ARGS, "path": "nope.py"})
    assert "file not found" in out["error"] and "main" in out["error"]


def test_read_file_missing_path(monkeypatch):
    fake = FakeGitHub()
    out, _ = run_builtin(monkeypatch, fake, "github_read_file", {**ARGS})
    assert "path" in out["error"]
    assert fake.requests == []  # no HTTP at all


# --- github_create_pr ---

def test_create_pr_defaults_base_to_default_branch(monkeypatch):
    fake = FakeGitHub()
    out, _ = run_builtin(monkeypatch, fake, "github_create_pr", {
        **ARGS, "title": "Feat", "head": "feat/x", "body": "does things",
    })
    assert out["number"] == 1 and out["state"] == "open"
    assert out["url"] == "https://github.com/acme/widget/pull/1"
    post = [r for r in fake.requests if r.url.path.endswith("/pulls")][0]
    body = json.loads(post.content)
    assert body["base"] == "main" and body["head"] == "feat/x" and body["body"] == "does things"


def test_create_pr_missing_head(monkeypatch):
    fake = FakeGitHub()
    out, _ = run_builtin(monkeypatch, fake, "github_create_pr", {**ARGS, "title": "T"})
    assert "head" in out["error"]


# --- guards (no HTTP) ---

def test_missing_secret_fails_before_http(monkeypatch):
    fake = FakeGitHub()
    seen = install(monkeypatch, fake, token=None)
    out = json.loads(asyncio.run(execute_tool(make_tool("github_create_branch"), {**ARGS, "branch": "b"}, {})))
    assert "GITHUB_TOKEN" in out["error"] and "Secrets panel" in out["error"]
    assert fake.requests == [] and seen["token"] is None  # client never constructed


def test_401_gets_token_hint(monkeypatch):
    fake = FakeGitHub()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    monkeypatch.setattr("app.engine.tools.get_secret", lambda name: "bad-token")
    monkeypatch.setattr(
        "app.engine.tools._github_client",
        lambda token: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://api.github.com",
            headers={"Authorization": f"Bearer {token}"}),
    )
    out = json.loads(asyncio.run(execute_tool(make_tool("github_create_pr"), {**ARGS, "title": "T", "head": "h"}, {})))
    assert "401" in out["error"] and "GITHUB_TOKEN" in out["error"]
