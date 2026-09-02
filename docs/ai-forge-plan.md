# AI Forge — Plan & Architecture Document

## Overview

A standalone web application for building AI agent workflows using LangGraph. Features a visual graph editor (drag-and-drop nodes and connections), file-based persistence, and hybrid custom code support.

**Target users:** Both developers and technical non-developers.
**Deployment:** Standalone server via `pip install` + CLI or Docker.

---

## Current Status (Phase 3 complete + post-Phase 3 increments)

> Last updated: 2026-09-01. Human-in-loop nodes are implemented end-to-end: LangGraph
> `interrupt()` pauses execution, the run persists its state via a SQLite checkpointer (paused runs
> survive restarts and are recovered on startup), and the frontend shows a paused state with an
> input form + resume button. The editor is a sidebar / master-detail layout with debounced
> auto-save; runs stream live over WebSocket. Secrets store, per-agent message isolation, async
> execution, timeout auto-fail, the Pending Approvals sidebar, and run-log persistence (event
> history survives restarts) are all shipped. Post-Phase 3: per-node **error branches** (opt-in red
> error handle → `type="error"` edge), the registry **`invoke` node**, **workflow templates**
> (`backend/app/templates/`, create-from-template in the UI — including the `github-pr-agent`
> demo), and the four `github_*` builtins (create_branch / read_file / write_file / create_pr)
> published to the registry as forge/* capabilities plus the forge/github-toolkit skill (2.0.0).
> Next up: test-connection endpoint (AI Forge); on the registry side — live refs + upgrade
> automation, run metrics → `evaluation` scores, SQLite → Postgres (ROADMAP.md R2 remainder).
> Use this section as the source of truth when resuming in a new session — it supersedes the
> phase notes below.

### What works today (shippable)
- **Backend engine (LangGraph)**: `start` → nodes → `end` graphs compile and run
  synchronously via `POST /api/workflows/{id}/run`.
- **Node types wired in the builder**: `agent` (with tool-calling loop), `conditional`,
  `transform` (`template` + `mapping` + `custom_function` modes), `custom_function`
  (RestrictedPython sandbox), `invoke` (registry capability invocation — tool kind
  executes directly; workflow kind expands into a call frame at build time).
- **Agent node**: reads `state["data"]` as user message (if no prior messages), calls LLM
  with system prompt, supports tool-calling loop. Output lands in `state["output"]`,
  `state["messages"]`, and `_node_outputs[<id>].content`.
- **Data flow**: `custom_function` writes declared `output_fields` into shared `data`;
  `transform` resolves nested dot-paths via `_resolve_path`; `transform` in
  `custom_function` mode runs a referenced node's sandbox code and writes the full result
  dict to `data[output_field]`. `run_workflow_sync` returns `data`.
- **State schema validation**: run input is validated against `workflow.state_schema.fields`
  (required-field presence + type conformance) before execution.
- **Agent tool loop**: agents with `tool_ids` get a bounded iteration loop — LLM call →
  tool_calls → execute (builtin / custom_function / http) → append results → repeat until
  no tool_calls or `max_iterations`. Tool schemas built from `ToolDefinition.parameters`.
- **Per-agent message isolation**: each agent node maintains its own conversation in
  `state["messages_by_node"][node_id]`. Sequential agents do NOT inherit each other's
  messages — they share only `state["data"]` (structured output). This enables multi-repo
  workflows where each agent works independently. Single-agent workflows are unaffected.
- **Tool execution boundary**: `custom_function` tools run in the RestrictedPython sandbox —
  compute-only (no `import`, no network, no filesystem; see `backend/app/sandbox/runner.py`).
  `http` and `builtin` tools run **outside** the sandbox (`backend/app/engine/tools.py`). The
  `http` handler supports URL templating from arguments (`{owner}/{repo}`), optional headers that
  can read secrets from the environment (`Authorization: Bearer ${GITHUB_TOKEN}`), and a
  per-request `timeout_seconds`. Args consumed by the URL are not re-sent in query/body.
- **Conditional routing**: conditional nodes AND edge-level conditions. `json_path` +
  `regex` implemented; `llm` raises `NotImplementedError`. Fallback resolution: preferred
  handle → `"default"` → (edge-level only) first static edge → `ConditionError`.
- **LLM layer**: OpenAI-compatible provider works (OpenAI / Ollama / llama.cpp / vLLM /
  LM Studio). Message serialization preserves `tool_calls` and `tool_call_id` for
  round-tripping. Anthropic raises `NotImplementedError` (Phase 4).
- **REST API**: full workflow CRUD + `run` + `validate`. See table below for status.
- **Run trace / debug panel**: every node is instrumented to emit `node_start`/`node_end` (with
  per-node `duration_ms` + a summarized output) and agent nodes emit `llm_call` events carrying token
  counts; totals + estimated cost are computed from model pricing. Events stream live over WebSocket
  (seq-numbered, replay-on-connect) to the editor's bottom panel which renders a per-node timeline
  (expandable output, LLM tokens, timing) plus the final output. Partial traces are preserved when a
  run fails mid-graph (`backend/app/engine/builder.py`, `frontend/.../RunPanel.tsx`).
- **Async execution**: `POST /run` returns 202 + `run_id` immediately; the graph runs in a worker
  thread via `asyncio.to_thread`. Clients subscribe over WebSocket for live events or poll
  `GET /runs/:id`. In-memory store (max 200 runs, pruned oldest-first).
- **Run-log persistence**: every emitted event is written through to an `events` table (plus a
  `runs` summary table) in `checkpoints.db` via a dedicated writer thread — synchronous SQLite on
  the event loop would deadlock with the per-run aiosqlite checkpointer. On startup, terminal runs
  are rebuilt from the store with their full event logs; `record._seq` resumes at the max persisted
  seq so post-restart events stay dedup-safe over WS.
- **Sidebar / master-detail editor**: persistent left rail lists workflows; selecting one loads it in
  the main pane (no page hop). Debounced auto-save (~800ms) + unmount flush. `key={id}` ensures fresh
  state per workflow.
- **Static validation**: `POST /api/workflows/{id}/validate` checks duplicate node ids,
  dangling edges, missing start/end, cycle detection, unreachable nodes, conditional
  branch-count mismatches, unknown model/tool references, transform custom_function
  reference integrity.
- **Frontend (Phase 2)**: editable React Flow canvas — drag/drop nodes from palette,
  draw/delete edges, delete nodes (cascade), per-node config panel (`ConfigPanel`),
  Validate + Save + Run buttons, optional collapsible JSON run-input box, run output
  display with status badge, save success toast. Conditional node handles update live
  when branches are added/removed (`useUpdateNodeInternals`).
- **Models panel**: modal CRUD for `ModelConfig` entries (name, provider, model, base_url,
  api_key_ref, default_temperature). Accessible via "Models" button in top bar. Agent
  nodes select a model from this list. Save persists models to workflow JSON.
- **Tools panel**: modal CRUD for `ToolDefinition` entries — name, description, parameters
  (name/type/required), and implementation (`builtin` / `custom_function` / `http`, each with its
  own config fields). Accessible via "Tools" button in top bar. Agent nodes select tools from this
  list (checkbox). Save persists tools to workflow JSON. Frontend-only; no backend change needed.
- **Secrets store**: `~/.ai-forge/secrets.json` (flat JSON, chmod 600) with env-var precedence
  (`os.environ` > file). Resolved via `get_secret(name)` — available in sandboxed custom functions
  and `${NAME}` placeholders in http tool headers. REST API: GET list (names + source only), PUT
  upsert, DELETE. Frontend: "Secrets" button opens a modal panel for CRUD. Accessible via "Secrets"
  button in top bar.
- **Workflow templates**: `backend/app/templates/` ships `sample-grade.json` (conditional routing demo),
  `sample-agent.json` (agent node with LLM call + transform), `sample-order-assistant.json`
  (agent + two sandboxed `custom_function` tools), and `github-pr-agent.json` (user story → GitHub PRs
  with a human approval gate). New workflows can be created from any template via `GET /api/templates`
  (sidebar "New from template" + EmptyState cards); instantiation is a normal `POST /workflows` with a fresh id.
- **Human-in-loop nodes**: `interrupt()` in builder pauses the graph; SQLite checkpointer
  (`AsyncSqliteSaver`, one connection per run on the shared file) with `thread_id` preserves state
  across restarts — startup recovery rebuilds paused runs from checkpoints and re-arms timeouts;
  `POST /runs/{id}/resume` sends human input via `Command(resume=...)`. Frontend: RunPanel shows
  paused state (purple indicator) with a dynamic
  `HumanInputForm` (text/textarea/select/boolean fields) + "Approve & Resume" button; on resume the
  event stream reconnects. ConfigPanel has a full editor for HIL nodes (input fields CRUD, approval
  toggle, timeout, output fields list). Validation checks output_fields presence and named inputs.
- **Tests**: backend suite green as of 2026-09-01 (347 tests, `python -m pytest -q`, incl. Capability Registry R1–R2); frontend 45 Vitest tests + typecheck/build clean.

### Engine data-flow gaps (Phase 2.1) — ALL DONE
- [x] **#1 Data-flow foundation** — custom_function write-back + nested dot-path reads
- [x] **#4 Transform `custom_function` mode** — run referenced node's sandbox code, write full result to `data[output_field]`
- [x] **#3 State schema wiring** — validate run input against `workflow.state_schema.fields`
- [x] **#2 Agent tool loop** — tool schema builder + executor, LLM message serialization, bounded iteration loop

### Phase 2.2 — ALL DONE
- [x] **#5 Models panel** — modal CRUD for ModelConfig in editor top bar. User adds model →
  selects on agent node → Run calls LLM end-to-end.
- [x] **Agent non-system message fix** — if no user/assistant messages exist in state,
  synthesize one from `state["data"]` (JSON-serialized) so the LLM API is always satisfied.
- [x] **Save toast** — ephemeral "Saved" indicator appears on successful save.
- [x] **Sample agent workflow** — `backend/app/templates/sample-agent.json` demonstrates agent → transform flow.

### What is deferred by design
- **Phase 2 (remaining)** — test-connection endpoint.
- **Phase 4** — container-based sandbox isolation, Anthropic provider, cost tracking,
  observability/Prometheus.

### Suggested next steps (Phase 2.3 / Phase 3)
- [x] **Tools panel** — UI for CRUD on `ToolDefinition` entries (done 2026-08-28; see "What works today").
- [x] **Harden the `http` tool** *(done 2026-08-28)* — URL path templating (`{owner}/{repo}`),
  headers with env-var secrets (`${GITHUB_TOKEN}`), per-request timeout. In `backend/app/engine/tools.py`;
  makes `http` tools usable against real APIs (GitHub, etc.) and unblocks the deferred experiment below.
- [x] **Run log / debug panel** *(done 2026-08-28)* — per-node execution trace (timing, intermediate
  output, LLM tokens + estimated cost) emitted during the run and rendered in the editor's bottom panel.
- [x] **Sidebar / master-detail editor** *(done 2026-08-28)* — persistent left rail + editor in main
  pane, debounced auto-save with unmount flush. See "Sidebar / master-detail editor — Design".
- [x] **Async execution** *(done 2026-08-28)* — POST /run returns 202 + runId immediately; WebSocket
  streams per-node events live (with seq-based replay/dedup); GET /runs/:id for polling fallback.
- [x] **Secrets store** *(done 2026-08-28)* — `~/.ai-forge/secrets.json` + env-var precedence;
  `get_secret()` in sandbox; `${NAME}` in http headers; REST API + frontend panel.
- [x] **Per-agent message isolation** *(done 2026-08-28)* — `messages_by_node` dict in state;
  sequential agents get fresh conversations, share only `data`. Unblocks multi-repo Option B.
- [x] **Human-in-loop nodes** *(done 2026-08-29)* — `interrupt()` + SQLite checkpointer,
  pause/resume API, frontend approval form + resume.
- [x] **Human-in-loop timeout auto-fail** *(done 2026-08-29)* — optional `timeout_seconds` on the
  node; an asyncio timer fails the run at the deadline (terminal `human_timeout` event) unless
  resumed first. Interrupt payload carries `timeout_seconds` + `requested_at`; RunPanel shows a
  live countdown and hides approve/reject once the client-side deadline passes.
- [x] **Pending Approvals sidebar** *(done 2026-08-29)* — `GET /api/runs/paused` lists paused runs
  (oldest first); the workflow sidebar shows a "Pending approvals" section with per-run countdowns,
  refreshed every 5s. Clicking an entry opens the workflow editor with that run loaded in the RunPanel
   via `?run=<id>` (reconnects to the event stream, so the approval form works).
- [x] **SQLite checkpointing** *(done 2026-08-29)* — replaced `MemorySaver` with
  `langgraph-checkpoint-sqlite`; each run opens its own `AsyncSqliteSaver` connection on the shared
  file (`~/.ai-forge/checkpoints.db`, WAL mode) because aiosqlite connections bind to one event loop.
  The HIL interrupt payload now carries `workflow_id`; on startup `recover_paused_runs()` finds
  threads whose latest checkpoint still has an `__interrupt__` write, rebuilds the record from the
  real graph's state snapshot, and re-arms the timeout (failing immediately if the deadline passed
   while down). Completed/resumed threads leave no pending interrupt, so they are not resurrected.
- [x] **Run-log persistence** *(done 2026-08-29)* — run events + a per-run summary row persist to
   `checkpoints.db` (write-through via a writer thread; sync SQLite on the event loop self-deadlocks
   with the aiosqlite checkpointer). After a restart, completed/failed runs are rebuilt with their
   full event log (`recover_finished_runs()`), so the RunPanel shows history for pre-restart runs and
   resumed HIL runs keep a continuous seq-numbered stream.
- [x] **Error branches** *(done 2026-08-30)* — per-node error edges for recovery routing.
  Design settled 2026-08-29; implemented as designed:
  - **Backend**: `Node.error_handling` (opt-in) marks a node as owning an error handle. When an
    instrumented node raises, the exception is converted into an `_error_info` state marker
    (cleared on success) and the node's router routes the run down its single `type == "error"`
    edge before normal routing; with no error edge the run fails as before. `GraphInterrupt`
    (HIL pause) always re-raises — it is never treated as a failure. Error edges are excluded
    from conditional branch matching, so a node can carry both condition branches and an error
    handle.
  - **Frontend**: red error handle on opted-in nodes (config-panel "Enable error handling"
    toggle); connecting from it creates an edge with `semanticType: 'error'`, styled red dashed;
    disabling the toggle drops any wired error edge.
  - **Validation**: `E_MULTIPLE_ERROR_EDGES` (max one per source), `E_ERROR_EDGE_FROM_START`,
    `E_ERROR_EDGE_NO_FALLBACK` (a success path must exist to fall through to), and
    `W_ERROR_EDGE_NO_OPTIN` (warning).
  - Tests: `backend/tests/test_error_branches.py` (routing, interrupt passthrough, all validation
    rules). Per-node retry logic (max_retries / backoff) remains a separate future increment.

### GitHub "user story → PR" agent template — DONE (2026-09-01)
Shipped as `backend/app/templates/github-pr-agent.json`, creatable from the sidebar / EmptyState
template pickers (see "Workflow templates" above). Final shape — a multi-node pipeline with a
human gate (variant of Option B; single-agent Option A was rejected because it skips the
governance moment, which is the point of the demo):

`start(request) → agent "implement" (branch + read/write files) → transform (report → data.implementation_report)
→ human_in_loop (approve/reject + optional pr_notes) → agent "finalize" (one PR per repo) → end`.

Design decisions:
- **Single free-form `request` input** — multi-repo capable ("fix X in octo/a and add Y to octo/b");
  repos are parsed as `owner/name`, never guessed. Trade-off accepted: no up-front state validation
  of the repo name; a missing repo surfaces as a clear builtin error in the run trace.
- **The implementer deliberately lacks `github_create_pr`** so the approval gate actually gates;
  the finalizer gets it explicitly plus the `forge/github-toolkit` skill (registry reuse story,
  shown with provenance in the ConfigPanel).
- **Handoff via `state["output"]` → transform template → `data.implementation_report`**, which the
  finalizer (isolated conversation) receives as its first message. The implementer's prompt requires
  a structured per-repo report line (`branch | files | PR title | PR body`) that the finalizer parses.
- Rejecting the approval fails the run; `timeout_seconds` is null (indefinite wait — no accidental
  demo failure). Requires the `GITHUB_TOKEN` secret and a configured model (the template ships a
  placeholder `gpt-4o-mini` entry to be pointed at the user's own model).
- **Read-before-write** (added after the first demo run, where the agent could not modify existing
  files because writes replace whole files): the new `github_read_file` builtin is in the implementer's
  tool set and its prompt mandates reading an existing file before modifying it.

Gaps to close first:
- [x] **URL templating for `http` tools** — done (see "Harden the http tool" above).
- [x] **Secrets store** — done; GitHub token stored in `~/.ai-forge/secrets.json`, referenced via
  `${GITHUB_TOKEN}` in headers or `get_secret("GITHUB_TOKEN")` in sandbox.
- [x] **`github_*` builtins** — done: `github_create_branch`, `github_read_file`, `github_write_file`,
  `github_create_pr` registered via `@register_builtin` in `backend/app/engine/tools.py`
  (token resolves via `get_secret("GITHUB_TOKEN")` and is never a tool argument; GHES via the
  `GITHUB_BASE_URL` env var). Published to the registry as forge/github-create-branch,
  -read-file, -write-file, -create-pr plus the forge/github-toolkit skill (one-click bundle of all
  four — the "tool collection" pattern: a skill whose tools[] pool-adds every member; toolkit is at
  2.0.0 because adding the read tool is a ref-set change = major per the settled breaking-change
  rule). Shipped alongside: import-time missing-secret warnings (both apply paths) and the
  publish-time composite `secrets_required` coverage check.

### Phase 2 — Design (completed)

> Scope decision: build **MVP first** = read-only React Flow canvas + per-node config panel
> + working Validate button, keeping the existing **sync** Run. Editable canvas shipped in
> a follow-up increment. Async + WebSocket streaming still deferred.

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
custom_function: `code`, `timeout_seconds`, input/output fields · human_in_loop: `approval_message`,
`approval_required`, `timeout_seconds`, `input_fields[]` (name/label/type/required/options),
`output_fields[]` · invoke: capability picker (`name@version`, tool/workflow kinds only) +
editable fields, `input_mapping[]` (source paths resolve from the state root — run inputs land
in `data.*`), `output_field`.

#### Verify
- `npx tsc --noEmit` (strict, `noUnusedLocals`/`noUnusedParameters`) and `npm run build`.

### Sidebar / master-detail editor — Design (planned)

> Goal: one page, no navigation hop. A persistent left rail lists workflows; the main pane is the
> editor. Selecting an item loads it in place. Deep links (`/workflows/:id`) keep working.
> Non-goal: a rich dashboard / gallery view (deferred).

#### Target routing
```
<Route element={<AppLayout/>}>              // sidebar + <Outlet/>, reads active id via useParams
  <Route index element={<EmptyState/>}/>   // "/" → nothing selected
  <Route path="workflows/:id" element={<WorkflowEditor/>}/>
</Route>
```

#### Files to create / change
| File | Action | Purpose |
|---|---|---|
| `frontend/src/components/layout/AppLayout.tsx` | new | Shell: `flex h-screen`; `<WorkflowSidebar activeId={id}/>` + `<main class="flex-1 min-w-0"><Outlet/></main>`. Gets `id` from `useParams()` (ancestor of the `:id` route → `{}` on `/`). |
| `frontend/src/components/layout/WorkflowSidebar.tsx` | new | The rail. Absorbs all `WorkflowList` logic (list + New + delete + search). |
| `frontend/src/pages/WorkflowEditor.tsx` | modify | Drop standalone chrome (back arrow, brand); root `h-screen` → `h-full`; add auto-save + dirty indicator. |
| `frontend/src/pages/WorkflowList.tsx` | delete | Create form + delete button move into the sidebar. |
| `frontend/src/App.tsx` | modify | Nested routes under `<AppLayout>`. |

#### Sidebar behavior
- `useQuery(['workflows'])`; each row = `<Link to="/workflows/:id">`, name (+ truncated description), active highlight when `activeId === id`, trash icon on hover.
- **New**: existing create mutation (`workflowsApi.create({id: workflow_${Date.now()}, ...})`) → `navigate('/workflows/'+id)` + invalidate `['workflows']`.
- **Delete**: confirm → `workflowsApi.delete`; if it was the open one, `navigate('/')`, else just invalidate.
- **Search** box (filter by name). Sort: name asc (v1). Optional: persist last-opened id to `localStorage` so `/` auto-selects it.

#### Editor changes
- Keep the per-workflow load effect (`WorkflowEditor.tsx:76-85`). Render with **`key={id}`** so each workflow gets a fresh instance — resets transient state (run panel, input JSON, validation) on switch.
- Remove the back arrow; navigation = click another row.

#### Auto-save / unsaved handling (the key decision)
Manual-only save is unsafe when switching is one click.
1. **Dirty tracking** — set `dirty=true` in each change handler (`handleConfigChange`, `handleConnect`, node add/delete, models/tools `onChange`); reset after the init effect populates state and after a successful save (avoids a false save on initial load).
2. **Debounced auto-save** — when dirty, ~800ms idle → existing `workflowsApi.update(id, payload)` (same body as the Save mutation at `WorkflowEditor.tsx:203`); on success clear dirty + brief "Saved" toast.
3. **Flush on switch** — with `key={id}`, switching unmounts the old editor. Add an unmount-cleanup effect: `return () => { if (dirtyRef.current) update(id, latestPayloadRef.current) }`; keep a `latestPayloadRef` fresh each render so the flush has the current serializable payload. Catches sub-800ms edits.
4. **UI** — replace the prominent Save button with a status indicator (`● Unsaved` / `Saving…` / `✓ Saved`); keep an explicit Save as secondary.

Residual edge (edit then switch within 800ms) is covered by #3. StrictMode double-invoke makes the flush fire twice — harmless (idempotent PUT).

#### Edge cases
- Deep link `/workflows/:id` → works; sidebar highlights it.
- Delete the open workflow → `navigate('/')`.
- Bad id → editor's existing "not found" state + a back-to-list affordance.
- `/` with no selection → `EmptyState` ("Pick a workflow or create one").

#### Out of scope / follow-ups
Collapsible sidebar, drag-to-reorder, per-workflow run history (the future dashboard), backend-generated ids.

#### Verify
No frontend unit tests — `npm run build` (tsc + vite) must pass. Manual: create → switch between two workflows (no hop, state resets); edit then immediately switch (auto-save persisted it); delete the open workflow (lands on `/`); reload a deep link.

#### Build order
1. `AppLayout` + nested routes + `EmptyState`; wire editor in with `key={id}`, drop standalone chrome.
2. `WorkflowSidebar` (list + New + delete + search); delete `WorkflowList.tsx`.
3. Auto-save + dirty indicator + unmount flush.
4. Polish: active highlight, empty state, not-found back-link; run build + manual pass.

Estimate ~1 day.

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

### Secrets & API Keys *(implemented)*
- **Env vars + config file** — `~/.ai-forge/secrets.json` (chmod 600), env vars take precedence
- **`get_secret()` helper** — Available in sandboxed custom function nodes and `${NAME}` in http headers
- **UI panel** — "Secrets" button in editor top bar; list (name + source), add/update, delete

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
- **Error branches** *(implemented)* — Opt-in error output edge per node for recovery logic; a
  node failure routes down its `type == "error"` edge if present, otherwise the run fails.
  Error edges styled as red dashed lines in the UI.
- **Retry logic** — Configurable per node (max retries, backoff strategy) *(future increment)*
- If retry exhausted AND no error edge, workflow fails

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
[done] POST   /api/workflows/:id/run      # Execute (async; returns 202 + run_id)
[done] GET    /api/runs/:runId            # Get run status + full result
[done] POST   /api/runs/:runId/resume     # Resume a paused run with human input
[done] WS     /api/runs/:runId/events     # Real-time execution stream (replay + live)
[done] POST   /api/workflows/:id/validate # Static graph validation
[done] GET    /api/secrets                # List secret names + source (values never returned)
[done] PUT    /api/secrets                # Upsert a secret (name, value)
[done] DELETE /api/secrets/:name          # Delete a secret
[plan] GET    /metrics                    # Prometheus metrics endpoint
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
