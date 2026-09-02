# AI Forge

A standalone web app for building **AI agent workflows** on [LangGraph](https://github.com/langchain-ai/langgraph). Workflows are directed graphs of nodes (agents, conditionals, transforms, sandboxed Python, human-in-loop gates) with file-based persistence. Author them in the visual React Flow editor or via the REST API.

> **Status:** Phase 3 + post-Phase 3 increments — the engine, REST API, static validation, and a full frontend (visual editor + config panels + run debug panel) are working end-to-end. Human-in-loop nodes (pause / resume / reject), async execution with live WebSocket streaming, a secrets store, per-agent message isolation, per-node error branches, workflow templates, and the `github_*` builtins are all implemented. A companion **Capability Registry** ([platform roadmap](./docs/ROADMAP.md): R1 complete, R2 in progress) adds identity, versioning, lifecycle, search, and the `invoke` node for calling registered capabilities — see [Capability Registry](#capability-registry).

---

## Current features

**Engine (works now)**
- LangGraph-based execution of `start → … → end` graphs, run **asynchronously** over HTTP with live event streaming.
- Node types: `agent` (with tool-calling loop; agent nodes can carry `skills[]` — folded into the system prompt + tools at graph-build — and a `prompt_ref` dot-path into the workflow's `prompts[]`), `conditional`, `transform` (`template`, `mapping`, `custom_function`), `custom_function` (sandboxed Python via RestrictedPython), `invoke` (calls a registry capability by `name@version`), and `human_in_loop` (pause / resume / reject).
- Conditional routing on both **nodes** and **edges**. The `json_path` and `regex` condition types work; `llm` is not implemented yet.
- **Error branches** — opt-in per-node error handle (config panel toggle); when a node fails, the run routes down its red-dashed `error` edge if one exists, otherwise the run fails. Human-in-loop pauses are never treated as failures.
- OpenAI-compatible LLM provider (OpenAI, Ollama, llama.cpp, vLLM, LM Studio).
- Per-agent message isolation — each agent keeps its own conversation; agents share only structured `data`.
- Run input validated against the workflow's `state_schema` (if defined) before execution.

**API (works now)**
- Full workflow CRUD + async `run` + `validate`, plus run retrieval, resume, and a secrets store. See [REST API](#rest-api).

**Frontend (works now)**
- Sidebar **master-detail** layout: workflow list with search, create, rename, delete.
- Full React Flow **graph editor**: drag-and-drop nodes, connect edges, per-node **config panel**.
- **Run debug panel**: live node/LLM trace, token + cost totals, expandable outputs, and a paused-state form for human-in-loop (approve / reject).
- Models, tools, and secrets management panels. Auto-save with an unsaved/saving/saved indicator.

**Capability Registry (R1 complete, R2 in progress — works now)**
- Capability Manifest schema (`schema/capability.py`) with six core kinds: `tool`, `prompt`, `model_profile`, `skill`, `agent`, `workflow`; composites reference other capabilities by `name@version`.
- Git-backed store + SQLite FTS5 index: immutable versions, lifecycle state machine (draft → review → approved → published → deprecated → retired).
- Publish (git commit + index sync), search, and use APIs on a separate server (`127.0.0.1:3010`).
- CLI: `ai-forge-registry serve | publish <files…> | seed` — publishing works offline; eleven sample capabilities (one per core kind + the `forge/*` GitHub set) ship in `registry/samples/`.
- **Capabilities view** in the frontend: browse/search, filter by kind, version history, and per-kind **Use in…** imports — pick a target workflow (and agent node for skills) and the capability is merged inline (`/use?inline=true` resolves skill/agent refs server-side).
- **R2 shipped:** the `invoke` node (call a registered capability by `name@version` — tool kind executes in place, workflow kind expands into the parent graph at build time behind a call frame) and publish-time governance checks (dependency resolution, kind stability, per-kind breaking-change detection that requires major semver bumps, composite secret coverage).

**Tests:** 347 backend tests passing (`python -m pytest -q`, as of 2026-09-01, incl. registry R1–R2); frontend 45 Vitest tests + typecheck/build clean.

---

## Architecture

```
Frontend (React + TS + Vite, React Flow)
   │  /api ────────────────▶ Backend (FastAPI, :3000)
   │                         ├─ API layer (CRUD, run, resume, validate, secrets)
   │                         ├─ Engine (LangGraph builder + runner)
   │                         ├─ LLM providers (OpenAI-compatible)
   │                         ├─ Sandbox (RestrictedPython)
   │                         └─ Persistence (JSON files in ~/.ai-forge)
   └─ /registry ───────────▶ Capability Registry (FastAPI, :3010)
                             ├─ Git store (~/.ai-forge/capabilities/)
                             └─ SQLite FTS5 index (~/.ai-forge/registry.db)
```

| Layer | Technology |
|---|---|
| Frontend | React + TypeScript + Vite, React Flow, TailwindCSS, React Query |
| Backend | FastAPI + Python 3.11+ |
| Workflow engine | LangGraph (SQLite checkpointer for pause/resume; paused runs survive restarts) |
| LLM layer | LangChain (OpenAI-compatible), abstract provider interface |
| Capability registry | FastAPI + aiosqlite; manifests in a local git repo (`~/.ai-forge/capabilities/`), FTS5 index in `~/.ai-forge/registry.db` |
| Persistence | One JSON file per workflow in `~/.ai-forge/workflows/`; run checkpoints in `~/.ai-forge/checkpoints.db`; secrets in `~/.ai-forge/secrets.json` |
| Sandboxing | RestrictedPython (containers planned for Phase 4) |

Full design and phase plan: [AI Forge Plan](./docs/ai-forge-plan.md) · platform vision & roadmap: [Roadmap](./docs/ROADMAP.md).

---

## Getting started

### Prerequisites
- Python **3.11+**
- Node.js **18+** (for the frontend)

### Run everything at once

```bash
./scripts/dev.sh
```

Boots the backend (`:3000`), the capability registry (`:3010`), and the Vite
dev server (`:5173`) in one terminal. Ctrl-C stops all three; if any service
crashes, the rest are stopped too. Per-service logs land in `.dev/<name>.log`.

### 1. Backend

```bash
cd ai-forge
python -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -e ".[dev]"
python backend/cli.py   # starts uvicorn on 127.0.0.1:3000 (auto-reload)
```

`backend/cli.py` boots the FastAPI app via uvicorn with auto-reload. Equivalently, run uvicorn directly from the `backend/` directory:

```bash
cd backend && uvicorn app.main:app --host 127.0.0.1 --port 3000 --reload
```

Workflows are stored as JSON files under `~/.ai-forge/workflows/`.

Verify it's up:

```bash
curl http://127.0.0.1:3000/health
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev         # Vite dev server on http://localhost:5173
```

The dev server proxies `/api` (including WebSocket upgrades) to the backend on port `3000`, and `/registry` to the capability registry on port `3010`. Open http://localhost:5173.

### 3. Capability Registry (optional)

The registry is a separate server; it holds versioned, searchable capability manifests (tools, prompts, model profiles, skills, agents, workflows):

```bash
python -m registry.cli serve        # or: ai-forge-registry serve  → 127.0.0.1:3010
python -m registry.cli seed         # publish the eleven bundled sample capabilities
curl http://127.0.0.1:3010/health
```

With the frontend running, the **Capabilities** view in the sidebar browses and uses them. `publish` and `seed` work offline — they validate manifests, write into the local git repo, commit once, and sync the index; no server needed. See [Capability Registry](#capability-registry).

### 4. Run the tests

```bash
python -m pytest -q          # backend (200 tests)
cd frontend && npm run lint  # frontend typecheck (tsc --noEmit)
```

---

## Using the app

Author workflows visually in the editor, or drive everything through the REST API. The flow is: **create a workflow (JSON) → validate it → run it with input**. Runs are asynchronous — `POST .../run` returns a `run_id` immediately and you follow progress over WebSocket or by polling.

> **How data flows through a run** — state shape, per-node read/write behavior, routing, condition syntax, and a worked example: [docs/data-flow.md](./docs/data-flow.md).

### Minimal no-LLM example (works out of the box)

This workflow needs no API keys. It takes a `score`, routes on a `json_path` condition, and produces different output for each branch.

```bash
curl -s http://127.0.0.1:3000/api/workflows \
  -H 'Content-Type: application/json' \
  -d '{
    "id": "grade",
    "name": "Grade checker",
    "nodes": [
      { "id": "start", "type": "start",
        "config": { "input_fields": ["score"] } },
      { "id": "route", "type": "conditional",
        "config": {
          "conditions": [
            { "type": "json_path", "expression": "$.data.score >= 90" }
          ],
          "default_branch": "low"
        } },
      { "id": "pass", "type": "transform",
        "config": { "mode": "template", "template": "PASSED", "output_field": "result" } },
      { "id": "fail", "type": "transform",
        "config": { "mode": "template", "template": "FAILED", "output_field": "result" } },
      { "id": "end", "type": "end", "config": { "output_fields": ["result"] } }
    ],
    "edges": [
      { "id": "e1", "source_node_id": "start", "source_handle": "default", "target_node_id": "route" },
      { "id": "e2", "source_node_id": "route", "source_handle": "pass",  "target_node_id": "pass", "type": "conditional" },
      { "id": "e3", "source_node_id": "route", "source_handle": "low",   "target_node_id": "fail", "type": "static" },
      { "id": "e4", "source_node_id": "pass",  "source_handle": "default", "target_node_id": "end" },
      { "id": "e5", "source_node_id": "fail",  "source_handle": "default", "target_node_id": "end" }
    ]
  }'
```

Validate it:

```bash
curl -s http://127.0.0.1:3000/api/workflows/grade/validate
```

Run it — this returns `202` with a `run_id`:

```bash
curl -s http://127.0.0.1:3000/api/workflows/grade/run \
  -H 'Content-Type: application/json' -d '{ "score": 95 }'   # -> { "run_id": "…" }
```

Then follow the run — either stream events live over WebSocket (`ws://…/api/runs/{run_id}/events`) or poll for the result:

```bash
curl -s http://127.0.0.1:3000/api/runs/{run_id}   # -> status + output_data once completed
```

### Agent example (needs an LLM)

Point an agent at any OpenAI-compatible endpoint. For a local model via Ollama:

```json
{
  "id": "summarizer",
  "name": "Summarizer",
  "models": [
    {
      "id": "local",
      "name": "Local Llama",
      "provider": "openai_compatible",
      "model": "llama3.1",
      "base_url": "http://localhost:11434/v1"
    }
  ],
  "nodes": [
    { "id": "start", "type": "start", "config": { "input_fields": ["text"] } },
    { "id": "agent", "type": "agent",
      "config": {
        "model_id": "local",
        "system_prompt": "You are a concise summarizer."
      } },
    { "id": "end", "type": "end", "config": { "output_fields": ["output"] } }
  ],
  "edges": [
    { "id": "e1", "source_node_id": "start", "source_handle": "default", "target_node_id": "agent" },
    { "id": "e2", "source_node_id": "agent", "source_handle": "default", "target_node_id": "end" }
  ]
}
```

Agent nodes support a tool-calling loop: give the node `tool_ids` referencing workflow-level `tools[]`, and it will call tools up to `max_iterations` times before producing its final answer. See `backend/app/templates/sample-order-assistant.json` for a complete example with sandboxed tools.

### Workflow templates

Bundled starter workflows live in `backend/app/templates/` (conditional routing, agent + transform,
sandboxed tools, and the GitHub "user story → PR" agent). New workflows can be created from any of
them via `GET /api/templates` — the sidebar's "New from template" picker and the EmptyState cards
fetch a template and create a new workflow from it with a fresh id.

The GitHub template makes a good first end-to-end run: set the `GITHUB_TOKEN` secret in the Secrets
panel, point its model entry at a real model, then run it with e.g. *"add a contact form to
octo/demo"* — it creates a branch and files, pauses for your approval (Pending Approvals sidebar),
then opens the PR and returns its URL.

### Sandboxed custom function

The `custom_function` node runs your Python in a RestrictedPython sandbox with a timeout. The code receives `state` and should write to `result`:

```json
{
  "id": "calc", "type": "custom_function",
  "config": {
    "code": "total = state['data'].get('a', 0) + state['data'].get('b', 0)\nresult['total'] = total",
    "timeout_seconds": 30,
    "input_fields": ["a", "b"],
    "output_fields": ["total"]
  }
}
```

---

## Capability Registry

A thin **system-of-record + discovery** layer above AI Forge (R1 of the [platform roadmap](./docs/ROADMAP.md)) — it is not a runtime. It gives shareable units of AI capability (tools, prompts, model profiles, skills, agents, workflows) an identity (`owner/name`), strict semver versions, ownership/governance metadata, a lifecycle stage, and search.

**How publishing works (R1):** `POST /registry/capabilities` or the CLI writes the manifest into a local git repo (`~/.ai-forge/capabilities/`), commits it, and syncs the SQLite FTS5 index. Publishing is a **direct commit to HEAD** — no remote, no branches, no PRs (single-user, localhost model). Review is tracked out-of-band through the lifecycle stage: `draft → review → approved → published → deprecated → retired`; only `published` versions surface as `latest`. Versions are immutable; git history provides provenance.

**CLI** (`ai-forge-registry`, or `python -m registry.cli`):

| Command | What it does |
|---|---|
| `serve` | Start the registry server on `127.0.0.1:3010` (default subcommand) |
| `publish <files…>` | Validate manifest JSON files, write them into the git repo, commit once, sync the index. Idempotent; same `name@version` with different content is rejected. Works offline. |
| `seed` | Publish the eleven bundled sample capabilities from `registry/samples/`. |

**Sample capabilities** (seeded by `ai-forge-registry seed`; they cross-reference each other into a composition chain):

| Sample | Kind | Notes |
|---|---|---|
| `acme/echo-tool` | tool | Builtin `echo` — self-contained, no network or LLM needed |
| `acme/courteous-assistant-prompt` | prompt | System template with `{{role}}` / `{{audience}}` placeholders |
| `acme/local-llama-profile` | model_profile | OpenAI-compatible profile pointing at a local Ollama endpoint |
| `acme/tool-selftest-skill` | skill | Instructions + references `echo-tool` |
| `acme/selftest-agent` | agent | References the model profile, echo tool, and self-test skill — the full dependency chain |
| `acme/echo-workflow` | workflow | A complete `start → agent → end` graph using the builtin echo tool |
| `forge/github-create-branch` / `-read-file` / `-write-file` / `-create-pr` | tool | The four `github_*` builtins (need the `GITHUB_TOKEN` secret) |
| `forge/github-toolkit` | skill | One-click bundle of all four GitHub tools — the "tool collection" pattern |

**Registry API** (base URL `http://127.0.0.1:3010`; proxied at `/registry` by the Vite dev server):

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/registry/capabilities` | List capabilities (one summary per name) |
| `GET` | `/registry/capabilities/{name}` | Detail — latest published manifest + full version history |
| `POST` | `/registry/capabilities` | Publish a manifest (git commit + index sync; returns 201) |
| `POST` | `/registry/capabilities/{name}/lifecycle` | Advance the stage (e.g. draft → review → published) |
| `GET` | `/registry/search?q=&kind=` | FTS search over name/description/tags |
| `GET` | `/registry/capabilities/{name}/use?version=latest&inline=true` | Resolved artifact payload for import; `inline=true` resolves skill/agent refs into a self-contained artifact |

Full design: [Capability Registry plan](./docs/capability-registry-plan.md).

---

## REST API

Base URL: `http://127.0.0.1:3000`

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `POST` | `/api/workflows` | Create workflow (body = workflow JSON) |
| `GET` | `/api/workflows` | List workflows |
| `GET` | `/api/workflows/{id}` | Get one workflow |
| `PUT` | `/api/workflows/{id}` | Update workflow |
| `DELETE` | `/api/workflows/{id}` | Delete workflow |
| `POST` | `/api/workflows/{id}/run` | Run asynchronously (body = input data; returns `202` + `run_id`) |
| `POST` | `/api/workflows/{id}/validate` | Static validation (returns issues + warnings) |
| `GET` | `/api/runs/{runId}` | Get run status + result (polling) |
| `POST` | `/api/runs/{runId}/resume` | Resume a paused run with human input |
| `WS` | `/api/runs/{runId}/events` | Live execution event stream (replays past events, then streams) |
| `GET` | `/api/secrets` | List secret names + source (values are never returned) |
| `PUT` | `/api/secrets` | Upsert a secret |
| `DELETE` | `/api/secrets/{name}` | Delete a secret |
| `GET` | `/api/templates` | List bundled workflow templates (id, name, description) |
| `GET` | `/api/templates/{id}` | Get one template's full workflow JSON |

### Workflow JSON shape

```jsonc
{
  "id": "string",              // required, unique
  "name": "string",            // required
  "description": "string",     // optional
  "schema_version": 1,         // for migrations
  "nodes": [ /* Node[] */ ],
  "edges": [ /* Edge[] */ ],
  "models": [ /* ModelConfig[] */ ],   // referenced by agent nodes
  "tools":  [ /* ToolDefinition[] */], // referenced by agent nodes via tool_ids
  "state_schema": { /* optional; validated against run input if defined */ }
}
```

**Node:** `{ "id", "type", "position": {"x","y"}, "config": {...} }` where `type` ∈ `start | end | agent | conditional | transform | human_in_loop | custom_function | invoke`.

**Edge:** `{ "id", "source_node_id", "source_handle", "target_node_id", "type": "static|conditional|error", "condition"? }`. For a conditional node, each non-`default` `source_handle` is a branch; the `conditions[i]` maps to the i-th branch edge (in workflow order). A `"default"` handle (or `default_branch`) is the fallback.

---

## Node types

| Type | Status | Notes |
|---|---|---|
| `start` / `end` | ✅ | Entry/exit; entry falls back to first node if no start present |
| `agent` | ✅ | Chat via OpenAI-compatible provider + tool-calling loop (`tool_ids`, `max_iterations`) |
| `conditional` | ✅ | Routes by condition; `json_path` and `regex` work, `llm` not implemented |
| `transform` | ✅ | `template`, `mapping`, and `custom_function` modes all work |
| `custom_function` | ✅ | Sandboxed Python (RestrictedPython), timeout enforced |
| `human_in_loop` | ✅ | Pauses the run for human input/approval; resume or reject via `POST /runs/{id}/resume` |
| `invoke` | ✅ | Calls a registry capability by `name@version`; tool kind executes in place, workflow kind expands into the parent graph at build time (per-run version pinning) |

---

## Known limitations

- **`llm` condition type** raises `NotImplementedError`; `json_path` and `regex` work.
- **Anthropic provider** not implemented (OpenAI-compatible only).
- **Run history is rebuilt from `checkpoints.db` on startup** — run summaries + full event logs persist there, so completed/failed runs and their traces survive restarts; paused runs are additionally recovered with their timeouts re-armed.

---

## Roadmap

- **Phase 2 (done):** React Flow graph editor + per-node config panel, async execution with WebSocket streaming, run log/debug panel.
- **Phase 3 (done):** human-in-loop nodes with pause/resume/reject, timeout auto-fail, a Pending Approvals sidebar, and SQLite checkpointing (paused runs survive restarts).
- **Post-Phase 3 (done):** per-node error branches (opt-in error edges), workflow templates with the create-from-template UI, and the `github_*` builtins (branch / read / write / PR).
- **Phase 4:** container-based sandbox isolation, Anthropic provider, cost tracking, observability/Prometheus.
- **Capability platform (R1 complete):** Find & Reuse — manifest schema, git-backed registry with search/publish/lifecycle, offline CLI + sample capabilities, and a Capabilities view in the frontend. R2 is in progress — the `invoke` node (call a registered capability by `name@version`) and publish-time governance checks have shipped; next up: live refs + upgrade automation, run metrics → evaluation scores, remote invocation over HTTP, SQLite → Postgres. R3 adds agent-native discovery. See [the Roadmap](./docs/ROADMAP.md).

Full detail in [the AI Forge Plan](./docs/ai-forge-plan.md).
