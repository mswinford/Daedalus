# AI Forge — Plan & Architecture Document

## Overview

A standalone web application for building AI agent workflows using LangGraph. Features a visual graph editor (drag-and-drop nodes and connections), file-based persistence, and hybrid custom code support.

**Target users:** Both developers and technical non-developers.
**Deployment:** Standalone server via `pip install` + CLI or Docker.

---

## Architecture

```
┌─────────────────────────────────────────────┐
│              Frontend (SPA)                 │
│  React + TypeScript + Vite                  │
│  React Flow (graph editor)                  │
│  TailwindCSS + shadcn/ui                    │
│  ┌─────────────┐  ┌─────────────────────┐  │
│  │  Canvas     │  │  Config Panel       │  │
│  │  (nodes,    │  │  (per-node settings) │  │
│  │   edges)    │  │                     │  │
│  └─────────────┘  └─────────────────────┘  │
└──────────────┬──────────────────────────────┘
               │ REST / WebSocket
┌──────────────▼──────────────────────────────┐
│           Backend (FastAPI)                 │
│  ┌─────────────┐  ┌──────────────────────┐  │
│  │  API Layer  │  │  Workflow Engine     │  │
│  │  (CRUD,     │  │  (LangGraph builder, │  │
│  │   execute)  │  │   runner, sandbox)   │  │
│  └─────────────┘  └──────────────────────┘  │
│  ┌──────────────────────────────────────────┐ │
│  │  Persistence (JSON files on disk)        │ │
│  │  Checkpointing (SQLite)                  │ │
│  └──────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

### Tech Stack

| Layer | Technology |
|---|---|
| Frontend framework | React + TypeScript + Vite |
| Graph editor | React Flow |
| Styling | TailwindCSS + shadcn/ui |
| Backend | FastAPI + Python |
| Workflow engine | LangGraph |
| LLM layer | LangChain (default), abstract call interface |
| Persistence | JSON files per workflow |
| Checkpointing | SQLite (LangGraph SqliteSaver) |
| Sandboxing | RestrictedPython or gVisor containers |

---

## Core Node Types

| Node | Purpose | Configurable in UI |
|---|---|---|
| **Agent** | LLM agent with system prompt + tools | Prompt, model, temperature, tools |
| **Tool** | Function the agent can call | Name, description, parameters, implementation |
| **Conditional** | Route based on output | Condition expression (JSON path, regex, LLM-based) |
| **Transform** | Data transformation | Template, field mapping, or custom function |
| **Human-in-loop** | Pause for human input/validation | Input schema, approval UI |
| **Custom Function** | User-written Python | Sandboxed Python code block |
| **Start / End** | Entry/exit points | Initial input schema |

### Graph Primitives

- Directed edges between nodes
- Conditional edges (multiple outputs from a node)
- Error edges (red dashed lines, executed on node failure)
- Shared state schema (workflow-level, nodes read/write fields)

---

## Feature Decisions

### Execution Model
- **Async with WebSocket streaming** — POST `/run` returns `runId`, client connects to WS for real-time events
- **Polling fallback** — GET `/runs/:runId` for historical runs and reconnection
- **SQLite checkpointer** — Workflow state persisted for crash recovery and human-in-loop pauses

### Secrets & API Keys
- **Env vars + config file** — `~/.ai-forge/secrets.json` (gitignored), env vars take precedence
- **`get_secret()` helper** — Available in custom function nodes
- **UI settings page** — Shows configured/missing keys

### Model Support
- **OpenAI-compatible** — Covers OpenAI, llama.cpp, Ollama, vLLM, LM Studio (base URL + API key)
- **Anthropic** — Native support
- **UI "Models" tab** — User adds models, agents select from the list
- **Cost tracking** — Toggleable per model, configurable pricing table

### Tool Scoping
- **Hybrid** — Tools defined at workflow level as a shared registry
- Each agent node selects which tools from the registry it can use
- UI: "Tools" tab in workflow settings + checkbox list per agent node

### State Schema
- **Hybrid: auto-infer + explicit overrides**
- Nodes declare outputs based on config, workflow state is the union
- "State Schema" panel shows all fields, users can rename, mark required, or add custom fields
- Node config shows "reads X fields, writes Y fields" for visibility

### LLM Layer Abstraction
- **Path A** — Abstract the LLM call layer, keep LangChain as default
- Define a clean `LLMProvider` protocol with adapters for LangChain and future frameworks
- LangGraph stays as the orchestrator

### Error Handling
- **Retry logic** — Configurable per node (max retries, backoff strategy)
- **Error branches** — Optional error output edge per node for recovery logic
- If retry exhausted AND no error edge, workflow fails
- Error edges styled as red dashed lines in the UI

### Debugging UX
- **Run log** — Timestamped events per run
- **Intermediate state** — Full workflow state after each node (from LangGraph checkpoints)
- **Input replay** — Re-run with same input that caused a failure
- **LLM output inspection** — Raw response, token counts, latency per call
- "Runs" tab per workflow, click a run to see timeline and node details

### Human-in-Loop
- **Custom input form** — Node defines what input it needs from the human
- **Full state panel** — Expandable, for power users to inspect everything
- **Configurable timeout** — Per node, default no timeout
- **UI surfacing** — "Pending Approvals" sidebar section + notification badge
- **Run ID labels** — Each pending task labeled with run ID and timestamp

### Deployment
- **pip install** — `pip install ai-forge`, then `ai-forge serve --port 3000`
- **Docker** — `docker run -p 3000:3000 ai-forge`
- JSON workflows stored in `~/.ai-forge/workflows/`

### Observability
- **Internal dashboard** — Run trends, slow nodes, error hotspots, token usage
- **Prometheus endpoint** — `/metrics` route, standard Prometheus format
- **Standard metrics:**
  - `runs.total` (Counter)
  - `runs.duration_ms` (Histogram)
  - `nodes.duration_ms` (Histogram)
  - `tokens.input` / `tokens.output` (Counter)
  - `llm.latency_ms` (Histogram)
  - `errors.count` (Counter)
  - `retries.count` (Counter)
- **Pricing** — Toggleable per model, configurable pricing table

### Custom Code Support
- **Hybrid** — Pre-built node types for 80% of cases
- **Custom Function node** — Sandboxed Python code block for edge cases
- Sandboxing via RestrictedPython or container-based isolation

### Persistence
- Each workflow = one JSON file in `~/.ai-forge/workflows/`
- Schema includes version number for migrations
- Export/import workflow as JSON
- Optional git integration (the workflows directory can be a git repo)

---

## Backend API

```
POST   /api/workflows              # Create workflow
GET    /api/workflows              # List workflows
GET    /api/workflows/:id          # Get workflow definition
PUT    /api/workflows/:id          # Update workflow
DELETE /api/workflows/:id          # Delete workflow
POST   /api/workflows/:id/run      # Execute workflow (returns run ID)
GET    /api/workflows/:id/runs/:runId  # Get run status + logs
WS     /ws/runs/:runId             # Real-time execution stream
POST   /api/workflows/:id/validate # Dry-run validation
GET    /metrics                    # Prometheus metrics endpoint
```

---

## Visual Editor UX

- **Left panel:** Node palette (draggable node types)
- **Center:** Infinite canvas with pan/zoom, nodes, bezier edges
- **Right panel:** Contextual config panel for selected node
- **Top bar:** Workflow name, save, run, validate buttons
- **Bottom panel:** Execution log / debug output when running

**Key interactions:**
- Drag node from palette → drop on canvas
- Click edge handle → drag to target node to create connection
- Click node → config panel opens on right
- Double-click edge → configure condition
- "Validate" button checks for cycles, missing connections, type mismatches

---

## Challenges & Risks

1. **LangGraph's state model is hard to abstract** — Making typed state intuitive for non-developers
2. **Graph editor complexity** — Custom node rendering, edge validation, minimap, undo/redo, responsive behavior
3. **Sandboxing Python** — RestrictedPython has limitations; containers add infrastructure complexity
4. **Competition** — LangGraph Studio, n8n, Dify, Flowise. Differentiator: LangGraph-native, file-based, custom code support
5. **Long-running execution** — Async execution, checkpointing, resume capability through web UI

---

## Differentiators

- **LangGraph-native** — Not just chaining LLM calls; full state machine workflows with conditional routing, human-in-loop, and checkpointing
- **File-based** — Developer-friendly, git-compatible, portable
- **Custom code escape hatch** — Sandboxed Python for edge cases
- **Local-first** — Works with local models (llama.cpp), no cloud dependency required
