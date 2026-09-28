# Global Runs Surface — Design

**Status:** all four phases shipped — eaf9ad7 (Phase 1), 7bfa4b9 (Phase 2), b92bb97 (Phase 3), 7a281cf (Phase 4)
**Supersedes:** audit item #3 "run-while-paused guard" (decision: multi-runs are allowed, no guard) and folds in audit item #5 "run history list".
**End goal:** multiple workflows running in parallel, visible and operable from the UI.

## 1. Current state

### Backend is already fully parallel-ready (no changes needed for concurrency)

- `RUNS` registry is keyed by `run_id` with no per-workflow exclusivity (`backend/app/runs/record.py:64`).
- Checkpointer uses `thread_id = run_id` (`executor.py:34`) — concurrent runs of the *same* workflow get separate checkpoint threads; WAL SQLite handles the concurrent connections.
- HIL timeouts, cancel events, and startup recovery are all keyed per-run.
- A summary row is upserted **from run start** (`api.py:80`, `_save_run_summary`) and on every status change — so the SQLite `runs` table covers running, paused, and terminal runs alike, across restarts.
- WS event streams replay from seq 0 for any run, terminal included — history replay needs no new streaming code.

### What already exists in the UI

- Sidebar **Pending Approvals** section: polls `GET /runs/paused` every 5 s, lists paused runs *across all workflows* with live countdowns, and opens one via `/workflows/:id?run=<runId>`.
- Editor `?run=` deep-link: fetches the run, streams it if non-terminal (`WorkflowEditor.tsx` `pendingRunId` effect).
- Cross-tab parallelism already works today: each tab is an independent editor with its own stream state; nothing in the frontend is global.

### The actual gaps

1. **Running runs are invisible** — only paused ones surface (Pending Approvals). No indication that a workflow has a live run.
2. **Single-run-per-editor view** — starting a second run of the same workflow replaces the first view with no way back to it.
3. **No `GET /runs`** — no general list endpoint, hence no history and no cross-workflow active-runs list.

## 2. Design decisions

**D1 — One new endpoint, `GET /runs`; leave `/runs/paused` alone.**
The paused endpoint carries interrupt-specific fields (`message`, `requested_at`, `timeout_seconds`) the sidebar already consumes. A general list endpoint is a different shape; extending the paused one would couple them.

**D2 — `GET /runs` merges SQLite + in-memory, in-memory winning per `run_id`.**
SQLite `runs` table = full history (survives restarts, exceeds the `MAX_RUNS=200` in-memory cap). In-memory `RUNS` = freshest status (summary writes go through a queue, ms-scale lag). Merge by `run_id`, overlay memory on top.

**D3 — No backend concurrency changes; double-resume is already safe.**
The resume endpoint flips `record.status` from `"paused"` to `"running"` with **no await between the check and the flip** (`api.py:100-125`: entry check, post-prepare re-check after `_prepare_run`, then synchronous flip) — atomic on the event loop. A second concurrent POST gets a clean 409. Frontend only needs to surface it (`apiErrorMessage` is already wired to the resume path).

**D4 — Cross-tab: polling is sufficient for v1, no push sync.**
A run resumed in tab B disappears from tab A's Pending Approvals on the next 5 s poll; a 409 on the losing tab's resume attempt is the explicit signal. Adding cross-tab WS/push sync is out of scope (see §7).

**D5 — Sidebar gets status dots on workflow rows, not a new section.**
Pending Approvals already provides detail + action for paused runs. Running runs need no per-run sidebar row (there's nothing to do until they pause or fail) — a dot suffices: pulsing green if any run is `running`, solid amber if only `paused`.

**D6 — In-editor run switching lives in the RunPanel header; starting a run never blocks.**
When the current workflow has ≥2 non-terminal runs, the RunPanel header shows a compact selector. Selecting another run reuses the exact `?run=` code path (extracted into one `showRun(runId)` helper — close stream, reset seq/finished refs, `getRun`, stream if live). Starting a new run while viewing another keeps today's behavior (view follows the new run); the switcher makes the old one reachable. **This replaces the guard: no confirm, no block.**

**D7 — History is its own phase, reusing the `?run=` replay path.**
A "History" list for the current workflow shows terminal runs; clicking navigates to `?run=<id>`, where the existing deep-link effect fetches the record and the WS replay delivers the full log (terminal runs included). No new rendering — the RunPanel already renders a finished run's events.

## 3. API spec

```
GET /runs?workflow_id=&status=&limit=
```

| Param | Notes |
|---|---|
| `workflow_id` | optional filter |
| `status` | optional, comma-separated: `running,paused,completed,failed,cancelled`; default = all |
| `limit` | default 100, max 500 |

**Response:** `[{run_id, workflow_id, status, started_at, completed_at, error, total_tokens_input, total_tokens_output, estimated_cost_usd}]` — summaries only; events come from the existing `GET /runs/{id}`. Sorted `started_at` desc.

**Implementation notes:**
- Plain sync `def` endpoint → FastAPI runs it in the threadpool, so a blocking short-lived SQLite read is safe (the loop-deadlock constraint applies to writes on the event loop, not threadpool reads). Same connection pattern as `_load_run_summary`.
- Overlay in-memory `RUNS` values per `run_id` for status freshness.

## 4. Frontend changes

### Phase 2 — Sidebar status dots (D5)

- New query `['runs', 'active']` → `GET /runs?status=running,paused`, `refetchInterval: 5000` (same cadence as the paused query).
- `SidebarRow` gains a dot derived from that list by `workflow_id`. Data is already in the sidebar component; no new files.
- The two queries (`/runs/paused` + `/runs?status=…`) overlap slightly; accepted — the paused one carries interrupt fields the active list deliberately doesn't.

### Phase 3 — In-editor run switcher (D6)

- Editor query `['runs', 'workflow', id, 'active']` → `GET /runs?workflow_id=<id>&status=running,paused`, `refetchInterval: 5000`, **enabled only while the RunPanel is open** (`run != null`) so idle editors poll nothing.
- Extract the `pendingRunId` effect body into `showRun(runId)` (stream close + `runLastSeqRef`/`runFinishedRef` reset + `getRun` + conditional stream). Both the deep-link effect and the switcher call it — the ref-reset dance exists in exactly one place.
- `RunPanel` gains props `runs: RunSummary[]` and `onSwitchRun(runId)`; header renders a `<select>` when `runs.length > 1`, current run highlighted.

### Phase 4 — History list (D7)

- "History" affordance in the editor toolbar → modal listing terminal runs for this workflow (`GET /runs?workflow_id=<id>&status=completed,failed,cancelled`).
- Row: status icon, started (relative), duration, tokens/cost, error snippet when failed.
- Click → `navigate(`/workflows/${id}?run=${runId}`)` — the existing path does the rest.

## 5. Concurrency & failure semantics (explicit)

| Scenario | Behavior |
|---|---|
| Two tabs resume the same paused run | First wins; second gets 409 "not paused", surfaced via `apiErrorMessage`. Pending Approvals row vanishes on next poll in both tabs. |
| Cancel while a run is being viewed | Terminal event over WS updates the panel (existing). |
| Server restart while a run is **running** | Not resurrected (recovery only re-arms paused runs — pre-existing behavior). Its summary row would otherwise stay `running` forever → **zombie fix in Phase 1**: at startup, any non-terminal summary whose `run_id` is not in `RUNS` is marked `failed` with error `"server restarted"`. |
| In-memory pruning (`MAX_RUNS=200`) | Affects only the freshness overlay; SQLite retains full history. `limit` bounds responses. |

## 6. Phases, effort, risk

| Phase | Scope | Effort | Risk |
|---|---|---|---|
| 1 | `GET /runs` endpoint + startup zombie-running cleanup + backend tests | S–M | Low — read-only merge; sync def runs in threadpool |
| 2 | Sidebar status dots | S | Trivial |
| 3 | In-editor run switcher + `showRun` extraction | M | Medium — stream/seq ref juggling; keep the reset logic in one helper and test it |
| 4 | History modal + replay navigation | M | Low — reuses the proven `?run=` path |

Phase 1 unblocks everything else; 2 is independent of 3–4 and can ship alone.

## 7. Out of scope

- Cross-tab live push sync (5 s polling covers v1; revisit if it ever feels slow).
- Resuming/restarting runs that were *running* at crash time — checkpoint data exists, but this needs its own design (graph rebuild + re-drive from last checkpoint).
- A global "runs dashboard" page — sidebar dots + per-workflow surfaces cover the need.
- Run comparison/diffing, per-run cost budgets.
