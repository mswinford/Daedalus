# AI Forge

A standalone web app for building **AI agent workflows** on [LangGraph](https://github.com/langchain-ai/langgraph). Workflows are directed graphs of nodes (agents, conditionals, transforms, sandboxed Python) with file-based persistence. A visual graph editor is in progress; the full engine and REST API are already usable today via the API.

> **Status:** Phase 1 complete — the workflow engine, REST API, static validation, and a partial frontend are working. The drag-and-drop graph canvas (Phase 2) is not built yet, so authoring workflows currently means writing JSON (or using the minimal list page). See [Current features](#current-features) and [Known limitations](#known-limitations).

---

## Current features

**Engine (works now)**
- LangGraph-based execution of `start → … → end` graphs, run synchronously over HTTP.
- Node types: `agent` (with tool-calling loop), `conditional`, `transform` (`template`, `mapping`, `custom_function`), `custom_function` (sandboxed Python via RestrictedPython).
- Conditional routing on both **nodes** and **edges**. The `json_path` and `regex` condition types work; `llm` is not implemented yet.
- OpenAI-compatible LLM provider (OpenAI, Ollama, llama.cpp, vLLM, LM Studio).

**API (works now)**
- Full workflow CRUD + `run` + `validate`. See [REST API](#rest-api).

**Frontend (partial)**
- Workflow **list** page: create, list, delete.
- Workflow **editor** shell with Save and Run buttons; the graph canvas is a placeholder.
- No Validate button in the UI yet (the endpoint exists and can be called directly).

**Tests:** 49 passing backend tests (`python -m pytest -q`); frontend typechecks clean.

---

## Architecture

```
Frontend (React + TS + Vite, React Flow) ──REST/WS──▶ Backend (FastAPI)
                                                        ├─ API layer (CRUD, run, validate)
                                                        ├─ Engine (LangGraph builder + runner)
                                                        ├─ LLM providers (OpenAI-compatible)
                                                        ├─ Sandbox (RestrictedPython)
                                                        └─ Persistence (JSON files in ~/.ai-forge)
```

| Layer | Technology |
|---|---|
| Frontend | React + TypeScript + Vite, React Flow, TailwindCSS |
| Backend | FastAPI + Python 3.11+ |
| Workflow engine | LangGraph |
| LLM layer | LangChain (OpenAI-compatible), abstract provider interface |
| Persistence | One JSON file per workflow in `~/.ai-forge/workflows/` |
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
ai-forge            # starts uvicorn on 127.0.0.1:3000 (auto-reload)
```

The CLI entry point is `ai-forge` (defined in `pyproject.toml`). You can also run it directly:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 3000 --reload
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

The dev server proxies `/api` and `/ws` to the backend on port `3000`, so both must be running. Open http://localhost:5173.

### 3. Run the tests

```bash
python -m pytest -q          # backend (49 tests)
cd frontend && npm run lint  # frontend typecheck
```

---

## Using the app

Because the visual editor is not finished, the practical way to use AI Forge today is through the REST API. The flow is: **create a workflow (JSON) → validate it → run it with input**.

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

Run it (both branches):

```bash
curl -s http://127.0.0.1:3000/api/workflows/grade/run \
  -H 'Content-Type: application/json' -d '{ "score": 95 }'   # -> PASSED
curl -s http://127.0.0.1:3000/api/workflows/grade/run \
  -H 'Content-Type: application/json' -d '{ "score": 40 }'   # -> FAILED
```

The run endpoint is **synchronous** and returns the full `WorkflowRun` object, including `output_data`, per-node `_node_outputs`, token counts, and any `error`.

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
| `POST` | `/api/workflows/{id}/run` | Run synchronously (body = input data) |
| `POST` | `/api/workflows/{id}/validate` | Static validation (returns issues + warnings) |

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
  "state_schema": { /* optional, not yet used by the engine */ }
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
| `human_in_loop` | ⏳ Phase 3 | Raises `NotImplementedError` |

---

## Known limitations

- **No visual graph editor yet** — author workflows as JSON. The React Flow canvas, per-node config panel, and Validate button are Phase 2.
- **Synchronous runs** — `POST .../run` blocks until completion. Async execution + WebSocket streaming is Phase 2.
- **`llm` condition type** raises `NotImplementedError`; `json_path` and `regex` work.
- **Anthropic provider** not implemented (OpenAI-compatible only).
- **`state_schema`** is defined but unused by the engine; state is a fixed internal shape.
- **Multiple agent nodes** in one run share a single conversation (`messages`), so a second agent continues the first's context instead of seeing the raw input again.

---

## Roadmap

- **Phase 2 (next):** React Flow graph editor + per-node config panel, Validate button UI, async execution with WebSocket streaming, run log/debug panel.
- **Phase 3:** human-in-loop nodes, SQLite checkpointing, pause/resume.
- **Phase 4:** container-based sandbox isolation, Anthropic provider, cost tracking, observability/Prometheus.

Full detail in [PLAN.md](./PLAN.md).
