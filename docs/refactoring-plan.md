# AI Forge — Refactoring Plan

Date: 2026-08-30 · Status: **Phase 1 not started**
Convention: check off items (`- [x]`) in this doc at commit time; keep the phase status line current.

## 1. Current architecture

Three deployables, one shared schema package:

| Piece | Stack | Role |
|---|---|---|
| `backend/app` (:3000) | FastAPI + LangGraph | Workflow CRUD, run execution, HIL pause/resume, secrets, WS event streaming |
| `registry/` (:3010) | FastAPI + git repo + SQLite FTS5 | Capability manifests: publish / search / inline-use |
| `frontend/src` (:5173) | React 18 + Vite + TS strict + React Flow | Editor canvas, config panels, run panel, capability browser |
| `schema/` (repo root) | Pydantic | Single source of truth for workflow/capability/run JSON; mirrored **by hand** in `frontend/src/lib/workflowTypes.ts` |

Control flow for a run: `POST /workflows/{id}/run` → validate + build `GraphBuilder` → LangGraph `StateGraph` executed in a worker thread with a per-run `AsyncSqliteSaver` (WAL, `~/.ai-forge/checkpoints.db`) → events fanned out via `RunRecord.emit()` to WS subscribers **and** queued to a dedicated writer thread persisting `runs`/`events` tables → HIL nodes raise `GraphInterrupt`, run marked `paused`, recovered from raw checkpoint SQL on restart.

Extension points today: node type = string literal + Pydantic union variant + if/elif in `builder._get_node_func` + per-type block in `validation.py` + ~9 hand-touched frontend sites. Provider = enum value + class + factory branch in `llm.create_provider`. Tool implementation = enum + branch in `execute_tool`.

## 2. Top findings

1. **Bug — model API keys are never resolved from the secrets store.** `backend/app/engine/builder.py:174` passes `model_config.api_key_ref` (a *name*, e.g. `"OPENAI_KEY"`) straight into the provider; `llm.py:53` uses it verbatim as the Bearer token. HTTP tools do it correctly (`tools.py:27` calls `get_secret`). Any workflow using a named secret for a model is silently broken (or sends the literal name to the API).
2. **Security — sandbox needs empirical verification (scope corrected 2026-08-30).** `backend/app/sandbox/runner.py` already uses RestrictedPython 8.5 (`compile_restricted_exec` + `safer_getattr` + guarded getitem/getiter), but nothing proves the configuration holds against bypasses — notably aliased builtins (`g = getattr; g(x, '__subclasses__')`) which skip the compile-time `_getattr_` rewrite. Probe tests required (R2).
3. **`backend/app/api/runs.py` (773 lines) is a god module** mixing the FastAPI router, in-memory store, SQLite persistence layer (writer thread/queue), restart-recovery logic, HIL timeout scheduling, and WS streaming. `_execute` (542–596) and `_resume` (599–653) are ~90% copy-paste.
4. **Frontend types drift from the backend by construction.** `frontend/src/lib/workflowTypes.ts` is a hand mirror of `schema/models.py`; drift already exists (`PromptDefinition.variables` missing, `name` non-optional). Generated JSON schemas have no sync check. Event-type literals duplicated again in `lib/api.ts:27–36`.
5. **Zero frontend tests**, and several pure functions (`graphTransform`, `capabilityImport.applyCapability`, countdown logic) are trivially testable.
6. **Dead schema surface:** `RetryConfig` + `retry` fields on 3 node configs + `RunEvent.type="retry"` exist but nothing implements retry (known deferred increment); `Workflow.schema_version` is declared and never read — no migration path exists.

## 3. Improvement opportunities

### R1. Resolve `api_key_ref` through the secrets store — P0, bug
- **Files:** `backend/app/engine/builder.py:174`, `backend/app/engine/llm.py:53`.
- **Approach:** in `_build_providers`, pass `get_secret(model_config.api_key_ref)` when set; let `llm.py` keep its `or "not-needed"` fallback. Add a test asserting the provider receives the *value*, not the name (and one for unset ref → `"not-needed"`).
- **Effort/risk:** tiny / near-zero (mirror the already-tested HTTP-tool path).

### R2. Empirically verify the sandbox — P0, security (scope corrected 2026-08-30)
- **Correction:** `backend/app/sandbox/runner.py` ALREADY uses RestrictedPython 8.5 (`compile_restricted_exec`, `safer_getattr`, guarded getitem/getiter, `safe_builtins`) — the original "plain compile() + blocklist" premise was wrong. The restricted compiler is in place; what's missing is *proof* it holds.
- **Files:** `backend/app/sandbox/runner.py`, `backend/tests/test_sandbox.py` (8 existing tests, all passing).
- **Approach:** (a) write escape-probe tests through the real runner: dunder subclass chains, and especially **aliased-builtin bypasses** (`g = getattr; g(x, '__subclasses__')` — RestrictedPython rewrites literal `getattr(...)` calls at compile time, but an aliased reference is a plain call and skips `_getattr_`); (b) if a probe escapes: close the specific hole (e.g. strip dangerous builtins from the namespace, custom `_getattr_` policy) and keep the probe as a regression test; (c) if nothing escapes: probes stay as green guard tests and R2 is done.
- **Known remaining weakness (documented in runner.py docstring):** timeout = daemon thread + `join(timeout)`; a timed-out execution keeps running unkillable — container/subprocess isolation deferred ("Phase 4"). Out of scope for R2 unless an escape is found.
- **Result (2026-08-30): verified clean.** 15 probes in `backend/tests/test_sandbox_escape_probes.py` — zero escapes. RP 8.5's `safe_builtins` has no `getattr`/`type`/`vars`/`dir`/`open`; dunders and `_`-names are rejected at compile time (transformer), with the runtime guards as a second layer. Residuals: (a) `default_guarded_getitem` is literally unrestricted — safe today only because no reachable object supports dunder-string subscripting; (b) **class statements in user code always error** (`NameError: '__metaclass__'`) — functional bug, track separately. Guardrail: never add `getattr`/`type` to `_EXTRA_BUILTINS` — that would re-open the aliased-reference bypass.

### R3. Decompose `runs.py` — P1, maintainability
- **Files:** `backend/app/api/runs.py` — persistence helpers (54–244), `RunRecord` (256–295), recovery (389–539), timeout scheduling (310–381), `_execute`/`_resume` (542–653), routes (656–773).
- **Approach:** split into `backend/app/runs/` package: `store.py` (SQLite schema + writer thread + flush/shutdown), `record.py` (`RunRecord` + emit + prune), `recovery.py` (`recover_paused_runs`, `recover_finished_runs`, raw-SQL helpers), `timeouts.py` (HIL scheduling); keep `api/runs.py` as thin routes. While splitting, dedupe `_execute`/`_resume` into one `_apply_result(record, result)` + shared terminal/failure emission (~60 lines deleted).
- **Caveats:** keep `flush_store`/`shutdown_store` semantics intact (the shutdown sentinel must call `task_done()` or `flush_store()` hangs after a TestClient restart cycle). Pure move + extract; all run/HIL/recovery tests must stay green.

### R4. Node-type extension: handler registry — P1, extensibility
- **Files:** `builder.py:177–194` (`_get_node_func`), edge special cases in `_build_edges` (507–545), per-type blocks in `validation.py` (148–265). Frontend: ~9 sites per new type.
- **Approach:** a `NodeHandler` protocol in `backend/app/engine/nodes/` — one module per type exposing `build(builder, node) -> Callable` and optionally `validate(node, ctx) -> list[ValidationIssue]`; a `dict[str, NodeHandler]` replaces the if/elif and validation's per-type blocks. Extract the agent tool-loop into an `AgentExecutor` class (testable without a graph). Keep the Pydantic union as-is.
- **Sequencing:** do after R3 (both restructure the engine).

### R5. Frontend: generated or parity-checked types — P1, correctness
- **Files:** `frontend/src/lib/workflowTypes.ts` vs `schema/models.py`; `scripts/generate_schema.py`.
- **Approach (recommended):** extend `generate_schema.py` to also emit TS types from the Pydantic JSON schemas into `frontend/src/lib/generated/`; hand-written file keeps only React Flow-specific wrappers. Cheaper alternative: a parity test asserting generated schema fields against checked-in expectations.
- **Note:** do R6 first so the generated types are covered by tests immediately.

### R6. Frontend: test the pure core — P1, testability
- **Approach:** add Vitest (no DOM needed), ~15–20 tests: `graphTransform` round-trips incl. conditional-handle derivation, `applyCapability` dedup rules per kind ("once per attachment point" matrix), countdown label edges.

### R7. Frontend: error states + WS robustness — P2, reliability
- **Problems:** `WorkflowEditor.tsx` load failure sets `error` state but never renders it (UI spins forever on "Loading workflow…"); `SecretsPanel` has no query error branch; sidebar delete-mutation errors swallowed; `streamRunEvents` (`api.ts:91–126`) has no `onerror`, no reconnect, silently drops malformed frames.
- **Approach:** render the existing `error` state (worst one first); shared `apiErrorMessage(e)` helper replacing the 4× repeated `e?.response?.data?.detail ?? e?.message`; WS `onerror` + bounded reconnect with seq-based replay (backend already replays on connect, so re-subscribe is safe).

### R8. Shared SQLite helper — P2, duplication
- **Files:** chmod-0600 + WAL open snippet copy-pasted in `runs.py:81–91`, `runner.py:24–30`, `registry/db.py`.
- **Approach:** one `open_secure_sqlite(path)` helper. Backend and registry are separate packages — either a small shared `common/` import root or accept the duplication knowingly with a comment (see open questions).

### R9. Registry: git+DB consistency as an invariant — P2
- **Files:** `registry/store.py` (`upsert_version` writes SQLite only), `registry/indexer.py` (hand-maintained FTS column list parallel to schema).
- **Why:** git/DB stay in sync only because the API path happens to call both; a future direct `upsert_version` call silently diverges.
- **Approach:** fold index update into the store write path (single writer), or add a startup `verify_index()` diffing git tree vs FTS rows and re-syncing. Add tests: direct-upsert consistency, deleted-manifest resync, circular skill-ref in `inline.py` (guard exists, untested).

### R10. Unify capability "already applied" logic — P2, duplication
- **Files:** `frontend/src/lib/capabilityImport.ts` (`applyCapability` presence checks) vs `CapabilityPicker.tsx:34–51` (`isPresent` for the Applied badge) — same rules, two code paths.
- **Approach:** export one `isCapabilityPresent(wf, capability, target?)` from `capabilityImport.ts`; picker and merge both call it.

### R11. Schema hygiene: `schema_version` — P2
- **File:** `schema/models.py:338` (declared, never read).
- **Approach:** implement the minimal loader hook (`Workflow.model_validate` wrapper dispatching on `schema_version`) now while there are zero migrations to write, or delete the field.

### R12. Provider client caching — P3, perf
- **File:** `llm.py:64,109` — a new `AsyncOpenAI` (and its httpx pool) is constructed per chat call inside an agent loop that may call 10×.
- **Approach:** create the client once in `__init__`.

### R13. Validation/builder rule duplication — P3, watch item
- `validation.py` deliberately mirrors builder structural rules (error-edge rules exist in both). Don't force-share now; R4's per-handler `validate()` registration is the natural convergence point. Until then, keep a test that builds+validates every sample workflow so divergence surfaces.

## 4. Extension-point audit (cost of adding a new node type today)

**Backend (4 files):** `schema/models.py` (Literal + union variant + config model) → `builder._get_node_func` elif + often edge-routing special cases → `validation.py` per-type block → JSON schema regen (`PYTHONPATH=. python scripts/generate_schema.py`).
**Frontend (9 sites):** `NodeType`, config interface, `NodeConfig` union, node interface, `NODE_META`, `ALL_NODE_TYPES`, `defaultConfig()`, `FlowNode.ICONS`, `subtitle()` switch, + a 60–120-line form in `ConfigPanel.renderConfig`.

Mitigating factor: TS strict + exhaustive switches make every site a compile error — costly but not silently broken. R4 fixes the backend half; R5's codegen removes 3 of the 9 frontend sites automatically.

Adding a **provider** is already fine (enum + class + one factory branch). Adding a **tool implementation** is the weakest spot: `execute_tool`'s if/elif over `ToolImplementationType` with untyped `config: dict[str, Any]` — when a 4th implementation type lands, give each implementation its own config model and a small dispatch table.

## 5. Roadmap

**Phase 1 — bugs & security (days, no structural risk)**
- [ ] R1: secret resolution for `api_key_ref` + test
- [x] R2: sandbox escape probe — verified clean, 15 guard tests added (scope correction: RestrictedPython was already in use)
- [ ] R7 (partial): render WorkflowEditor load error; shared `apiErrorMessage`
- [ ] R12: cache the OpenAI client

**Phase 2 — structure (sequenced)**
- [ ] R3: split `runs.py` into `app/runs/` package + dedupe `_execute`/`_resume`
- [ ] R4: node handler registry + `AgentExecutor` extraction
- [ ] R8: shared SQLite helper · R10: unified capability presence check
- [ ] R6: Vitest setup + pure-function tests (before R5 lands)

**Phase 3 — consistency & extension surface**
- [ ] R5: TS type codegen from Pydantic schemas (+ delete hand-mirrored types)
- [ ] R9: registry single-writer invariant + missing tests
- [ ] R11: `schema_version` migration hook (or removal)
- [ ] R7 (rest): WS reconnect with seq replay; remaining error states

## 6. Concrete steps for the two P0 items

**R1 (secret resolution):** in `builder.py._build_providers`, replace `"api_key": model_config.api_key_ref` with a lookup: `get_secret(ref)` if `ref` is set, else `None`; let `llm.py` keep its `or "not-needed"` fallback. Tests: seed a secret via the existing secrets test utilities, build a workflow whose model references it by name, assert the constructed provider's `api_key` equals the secret *value*; second test for unset ref → `"not-needed"`.

**R2 (sandbox):** write probe tests through the real `run_sandboxed`: (1) direct dunder subclass chain — expect blocked by `safer_getattr`; (2) aliased-builtin bypass (`g = getattr; g(x, '__subclasses__')`) — the suspected real hole; (3) any other reachability probes found while reading the guards. Escaping probes get `xfail(strict=True)` (suite stays green, fails loudly if fixed); blocked ones are plain passing tests. If a hole is found: close it in `runner.py` (namespace/policy fix), convert the probe to a green regression test. The unkillable-timeout-thread weakness stays documented, out of scope.

## 7. Testing plan

- **Per refactor:** `python -m pytest -q` after every phase step; R3/R4 must land with zero behavior change (all run/HIL/recovery/error-branch tests green before and after).
- **New backend tests:** secret→provider resolution (R1); sandbox escape probe (R2); runs-store unit tests once `store.py` is isolated (flush/shutdown, prune boundary at MAX_RUNS); registry: direct-upsert git/DB consistency, deleted-manifest resync, circular skill ref; schema-sync test asserting `generate_schema.py` output matches committed JSON.
- **New frontend tests:** Vitest for `graphTransform` round-trips, `applyCapability` dedup matrix, countdown labels (R6).
- **Manual smoke after Phase 2:** full dev stack (`./scripts/dev.sh`) — create workflow with HIL + timeout, pause, restart backend mid-pause, verify recovery; run a tool-calling agent against a local model.

## 8. Do NOT refactor

- The per-run event loop + per-run `AsyncSqliteSaver` in `runner.py` — deliberate workaround for aiosqlite's loop-binding; "cleaner" shared-saver designs are dead ends.
- Raw SQL over the `writes` table in recovery — brittle-looking but the only way to see pending interrupts without the real graph; keep, with a comment pinning the LangGraph version assumption.
- The writer-thread persistence design in runs.py — synchronous SQLite on the loop self-deadlocks; the queue/thread is correct, only its *location* should move (R3).
- `Command(resume={id: value})` map-form workaround in `resume_workflow` — LangGraph 1.2.x bug workaround; don't "simplify" it.
- The two-app split (backend vs registry) and the git+SQLite registry design — architecturally sound; only tighten the write path (R9).
- Introducing a DI framework, an ORM, or microservices — no current pain justifies them.
- `RetryConfig` — intentionally deferred; leave the schema surface, implement as its own increment.

## 9. Open questions

1. **Sandbox threat model:** is custom_function code ever written by someone other than the workflow owner? Decides R2's scope (RestrictedPython policy vs subprocess/container vs documented "trusted only").
2. **`api_key_ref` semantics when the named secret doesn't exist:** fail fast at build time, or fall back to treating the string as a literal key (current accidental behavior)? Leaning fail-fast with a clear validation error.
3. **TS codegen (R5) vs parity test:** codegen is the durable fix but adds a build step — acceptable?
4. **Shared `common/` package** for the SQLite helper (R8): fine to add another import root, or keep backend/registry independent and accept the duplication?
5. **Is Anthropic-provider support near-term?** If yes, R4 should land first so the provider factory gets a second real consumer; if no, the current factory is adequate.
6. **Multi-process deployment ever planned?** The module-level `RUNS` dict + single writer thread assume one process; if horizontal scaling is on the horizon, Phase 2's store extraction should target a swappable backend from day one.
