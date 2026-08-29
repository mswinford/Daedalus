# AI Forge

A standalone web app for building **AI agent workflows** on [LangGraph](https://github.com/langchain-ai/langgraph). Workflows are directed graphs of nodes (agents, conditionals, transforms, sandboxed Python, human-in-loop gates) with file-based persistence. Author them in the visual React Flow editor or via the REST API.

> **Status:** Phase 3 — the engine, REST API, static validation, and a full frontend (visual editor + config panels + run debug panel) are working end-to-end. Human-in-loop nodes (pause / resume / reject), async execution with live WebSocket streaming, a secrets store, and per-agent message isolation are all implemented. See [Current features](#current-features) and [Known limitations](#known-limitations).

---

## Current features

**Engine (works now)**
- LangGraph-based execution of `start → … → end` graphs, run **asynchronously** over HTTP with live event streaming.
- Node types: `agent` (with tool-calling loop), `conditional`, `transform` (`template`, `mapping`, `custom_function`), `custom_function` (sandboxed Python via RestrictedPython), and `human_in_loop` (pause / resume / reject).
- Conditional routing on both **nodes** and **edges**. The `json_path` and `regex` condition types work; `llm` is not implemented yet.
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

**Tests:** 111 passing backend tests (`python -m pytest -q`); frontend typechecks clean.

---

## Architecture

```
Frontend (React + TS + Vite, React Flow) ──REST/WS──▶ Backend (FastAPI)
                                                         ├─ API layer (CRUD, run, resume, validate, secrets)
                                                         ├─ Engine (LangGraph builder + runner)
                                                         ├─ LLM providers (OpenAI-compatible)
                                                         ├─ Sandbox (RestrictedPython)
                                                         └─ Persistence (JSON files in ~/.ai-forge)
```

| Layer | Technology |
|---|---|
| Frontend | React + TypeScript + Vite, React Flow, TailwindCSS, React Query |
| Backend | FastAPI + Python 3.11+ |
| Workflow engine | LangGraph (in-memory `MemorySaver` checkpointer for pause/resume) |
| LLM layer | LangChain (OpenAI-compatible), abstract provider interface |
| Persistence | One JSON file per workflow in `~/.ai-forge/workflows/`; secrets in `~/.ai-forge/secrets.json` |
| Sandboxing | RestrictedPython (containers planned for Phase 4) |

Full design and phase plan: [PLAN.md](./PLAN.md).

---

## Getting started

### Prerequisites
- Python **3.11+**
- Node.js **18+** (for the frontend)

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

The dev server proxies `/api` (including WebSocket upgrades) to the backend on port `3000`, so both must be running. Open http://localhost:5173.

### 3. Run the tests

```bash
python -m pytest -q          # backend (111 tests)
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

Agent nodes support a tool-calling loop: give the node `tool_ids` referencing workflow-level `tools[]`, and it will call tools up to `max_iterations` times before producing its final answer. See `samples/sample-order-assistant.json` for a complete example with sandboxed tools.

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

**Node:** `{ "id", "type", "position": {"x","y"}, "config": {...} }` where `type` ∈ `start | end | agent | conditional | transform | human_in_loop | custom_function`.

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

---

## Known limitations

- **`llm` condition type** raises `NotImplementedError`; `json_path` and `regex` work.
- **Anthropic provider** not implemented (OpenAI-compatible only).
- **`error`-typed edges** are defined in the schema but not wired to failure handling — node exceptions currently fail the run (emitted as a fatal `node_error` event).
- **Checkpointing is in-memory** (`MemorySaver`) — paused runs must be resumed within the same process; SQLite persistence is deferred.
- **Human-in-loop timeout** (`timeout_seconds`) is metadata only; auto-fail-on-timeout is not yet enforced.

---

## Roadmap

- **Phase 2 (done):** React Flow graph editor + per-node config panel, async execution with WebSocket streaming, run log/debug panel.
- **Phase 3 (mostly done):** human-in-loop nodes with pause/resume/reject; deferred: SQLite checkpointing, auto-fail-on-timeout.
- **Phase 4:** container-based sandbox isolation, Anthropic provider, cost tracking, observability/Prometheus.

Full detail in [PLAN.md](./PLAN.md).
