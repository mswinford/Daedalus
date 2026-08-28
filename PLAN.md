# AI Forge — Plan & Architecture Document

## Overview

A standalone web application for building AI agent workflows using LangGraph. Features a visual graph editor (drag-and-drop nodes and connections), file-based persistence, and hybrid custom code support.

**Target users:** Both developers and technical non-developers.
**Deployment:** Standalone server via `pip install` + CLI or Docker.

---

## Current Status (Phase 1 complete)

> Last updated after the validate-endpoint commit (`864367d`). Use this section as the
> source of truth when resuming in a new session — it supersedes the phase notes below.

### What works today (shippable)
- **Backend engine (LangGraph)**: `start` → nodes → `end` graphs compile and run
  synchronously via `POST /api/workflows/{id}/run`.
- **Node types wired in the builder**: `agent`, `conditional`, `transform`
  (`template` + `mapping` modes), `custom_function` (RestrictedPython sandbox).
- **Conditional routing**: conditional nodes AND edge-level conditions. Only the
  `json_path` condition type is implemented; `regex` and `llm` raise
  `NotImplementedError`. Fallback resolution: preferred handle → `"default"` →
  (edge-level only) first static edge → `ConditionError`.
- **LLM layer**: OpenAI-compatible provider works (OpenAI / Ollama / llama.cpp / vLLM /
  LM Studio). Anthropic raises `NotImplementedError` (Phase 4).
- **REST API**: full workflow CRUD + `run` + `validate`. See table below for status.
- **Static validation**: `POST /api/workflows/{id}/validate` checks duplicate node ids,
  dangling edges, missing start/end, cycle detection, unreachable nodes, conditional
  branch-count mismatches, unknown model/tool references.
- **Frontend (partial)**: `src/pages/WorkflowList.tsx` (create/list/delete) and
  `src/pages/WorkflowEditor.tsx` (shell with Save + Run buttons). The graph canvas is a
  **placeholder** — no React Flow yet. There is **no Validate button in the UI** yet (the
  API + client method exist). `@xyflow/react` v12.11.5 is installed but unused; there is no
  `src/components/` dir yet. Only files: `App.tsx`, `main.tsx`, `index.css`, `lib/api.ts`,
  the two pages above.
- **Tests**: 49 passing (`python -m pytest -q`). Frontend typechecks clean (`tsc --noEmit`).

### Known gaps vs. this plan (Phase 1 leftovers)
- `custom_function` can read state but its result only lands in `output` /
  `_node_outputs`; it cannot write back into the shared `data.*` fields.
- Agent nodes ignore `tool_ids` and `max_iterations` — a single chat call, no tool loop.
- `state_schema` field is defined but not used by the builder (state is a fixed TypedDict).
- Transform `custom_function` mode is not wired in the builder (only `template`/`mapping`).

### What is deferred by design
- **Phase 2** — React Flow graph editor, per-node config panel, Validate button UI,
  async execution + WebSocket streaming, run log/debug panel.
- **Phase 3** — human-in-loop nodes, SQLite checkpointing, pause/resume.
- **Phase 4** — container-based sandbox isolation, Anthropic provider, cost tracking,
  observability/Prometheus.

### Phase 2 — Design (decided, not started)

> Scope decision: build **MVP first** = read-only React Flow canvas + per-node config panel
> + working Validate button, keeping the existing **sync** Run. Defer editable canvas
> (add/delete/connect nodes & edges) and async + WebSocket streaming to a later increment.

#### Files to create / change
| File | Action | Purpose |
|---|---|---|
| `frontend/src/lib/workflowTypes.ts` | new | TS types mirroring `schema/models.py`: `NodeType`, `EdgeType`, `ConditionConfig`, all node config interfaces, `WorkflowDoc`. |
| `frontend/src/lib/graphTransform.ts` | new | Backend ⇄ React Flow conversion (`nodesToRF`, `edgesToRF`, `rfToNodes`, `rfToEdges`), conditional handle derivation (`sourceHandlesFor`), derived display (`applyDerived`). |
| `frontend/src/components/flow/FlowNode.tsx` | new | One generic custom node registered under all 7 type keys; renders handles per type. |
| `frontend/src/components/flow/ConfigPanel.tsx` | new | Right-side per-type config editor forms. |
| `frontend/src/pages/WorkflowEditor.tsx` | rewrite | Top bar (Back, Validate, Save, Run), left palette, center `<ReactFlow>` canvas, right ConfigPanel. |
| `frontend/src/lib/api.ts` | modify | Tighten `Workflow.nodes`/`edges` from `any[]` to the typed arrays above. |

#### Data mapping (backend ⇄ React Flow)
- Node `{id,type,position:{x,y},config}` → RF node `{id, type:<same string>, position, data:{config, ...derived}}`.
  - `NodeType` is a `typing.Literal[...]`, **not** an enum — use plain strings (`"start"`, `"agent"`, …).
- Edge `{id,source_node_id,source_handle,target_node_id,type,condition}` → RF edge
  `{id, source, sourceHandle, target, data:{semanticType, condition}}`.
  - RF `type` is always `'default'`; the backend semantic type (`static`/`conditional`/`error`)
    lives in `data.semanticType`.

#### Conditional node handles (the tricky part)
- Contract: outgoing edges with `source_handle != "default"` are **branches**, matched
  **positionally** to `config.conditions[i]` ↔ `branches[i]`. Fallback = `default_branch`,
  else the `"default"` handle, else `ConditionError`.
- Handle names are **derived, not stored**: for condition `i`, name = `branches[i].sourceHandle`
  if present else `branch_{i+1}`; plus one default handle = `default_branch ?? "default"`.
  This keeps existing named branches working AND lets a new branch appear when a condition is added.

#### Derived display pattern
- Keep raw `nodes`/`edges` in React Flow state (`useNodesState`/`useEdgesState`).
- Compute `displayNodes`/`displayEdges` via `useMemo(applyDerived)` — injects
  `data.branchHandles` (conditional nodes) and validation flags. Pass the derived arrays to `<ReactFlow>`.

#### Validation highlighting
- Validate button → `workflowsApi.validate(id)` → `ValidationResult{errors,warnings}`.
- Build a `Map<string,'error'|'warning'>` keyed by `node_id`/`edge_id`; errors = red ring/stroke,
  warnings = amber. Surface the issue list in a panel/toast.

#### Config panel forms (per type)
start: `input_fields` · end: `output_fields` · agent: `model_id`, `system_prompt`, `temperature`,
`tool_ids`, `max_iterations` · conditional: `conditions[]` (`type` json_path/regex/llm + `expression`),
`default_branch` · transform: `mode` (template/mapping/custom_function) + fields ·
custom_function: `code`, `timeout_seconds`, input/output fields · human_in_loop: Phase 3 — render as
"not yet supported" for now.

#### Verify
- `npx tsc --noEmit` (strict, `noUnusedLocals`/`noUnusedParameters`) and `npm run build`.

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

Status: `[done]` = implemented and tested, `[plan]` = not yet built.

```
[done] POST   /api/workflows              # Create workflow
[done] GET    /api/workflows              # List workflows
[done] GET    /api/workflows/:id          # Get workflow definition
[done] PUT    /api/workflows/:id          # Update workflow
[done] DELETE /api/workflows/:id          # Delete workflow
[done] POST   /api/workflows/:id/run      # Execute (sync; returns full run object)
[plan] GET    /api/workflows/:id/runs/:runId  # Get run status + logs
[plan] WS     /ws/runs/:runId             # Real-time execution stream
[done] POST   /api/workflows/:id/validate # Static graph validation
[plan] GET    /metrics                    # Prometheus metrics endpoint
```

> Note: `POST .../run` is currently **synchronous** — it blocks until the workflow
> finishes and returns the full `WorkflowRun`. The async + run-ID + WebSocket model in
> this plan is Phase 2.

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
