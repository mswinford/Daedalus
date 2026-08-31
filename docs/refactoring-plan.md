# AI Forge — Refactoring Plan (complete)

Date: 2026-08-30 → 2026-08-31 · Status: **Complete** — all 12 items landed (`fa72a7d`…`5c11d69`). This doc is now a completion record; the "Do NOT refactor" section and residuals below remain active guidance.

## Results

| Item | Result | Commit |
|---|---|---|
| R1 — P0 bug: model `api_key_ref` never resolved from secrets store | `_build_providers` resolves via `get_secret()`; missing secret falls back to the raw string (frontend ModelForm pastes literal keys, so no fail-fast); 5 tests in `test_provider_secrets.py` | `fa72a7d` |
| R2 — P0 security: empirically verify sandbox | Verified clean under RestrictedPython 8.5 — 15 escape probes, zero escapes; probes kept as guard tests. (Scope correction: the restricted compiler was already in place; what was missing was proof.) | `de76258` |
| R3 — decompose god module `runs.py` (773 lines) | Split into `app/runs/` package (`store`, `record`, `recovery`, `timeouts`, `executor`); `_execute`/`_resume` merged into one `_drive()`; routes stay thin in `api/runs.py` | `4c3f9e1` |
| R4 — node-type handler registry | `engine/nodes/` package: `base.py` (protocols), one module per type, `HANDLERS` registry; agent tool-loop extracted into unit-testable `AgentExecutor`; builder.py 572→274 lines. New node type = 1 module + 1 registry line | `d347f4e` |
| R5 — frontend types drift from backend by construction | TS codegen: quicktype-core (`npm run generate:types`) reads both JSON schemas → `workflowTypes.generated.ts`; thin hand layers in `workflowTypes.ts`/`api.ts`. Drift audit fixed a stale `PromptDefinition` mirror. After editing `schema/models.py`: run `scripts/generate_schema.py` **and** `npm run generate:types` | `48d3daf` |
| R6 — zero frontend tests | Vitest 3 (`^3` pinned — v4 needs Vite 6+), no jsdom; 29 tests over `graphTransform`, `capabilityImport`, countdown logic | `56938ae` |
| R7 — frontend error states + WS robustness | Partial: workflow-load error rendered + shared `apiErrorMessage`. Rest: `streamRunEvents` bounded reconnect (exponential backoff 500ms→8s, 5 attempts per failure episode, resets on message receipt; server replay from seq 0 makes re-subscribe safe); SecretsPanel query-error branch; sidebar delete-mutation error. 32 frontend tests | `5ae87e7` + `5c11d69` |
| R8 — shared SQLite helper | Scoped down after re-reading: real duplication was only the chmod loop. Backend-local `app/sqlite_util.py::secure_owner_only(path)`; no new import root, registry untouched (see residual below) | `fa39917` |
| R9 — registry git+DB consistency | Re-scoped: single-writer invariant already held (both write paths go git-commit → full `sync_from_repo`). Real gap was **no deletion propagation** — `sync_from_repo` now prunes rows absent from the repo (guarded by ≥1 commit; conflict rows kept); publish non-atomicity documented (self-heals on next rescan). +5 tests | `008cd09` |
| R10 — duplicated capability "already applied" logic | One exported `isCapabilityPresent(wf, kind, key, targetNodeId?)`; `applyCapability` and the picker both delegate | `54fb5e2` |
| R11 — `schema_version` declared but never read | `load_workflow()` dispatches an ascending `MIGRATIONS` chain (empty at v1), loud error on invalid/future versions; wired into both load sites. Both save paths stamp `CURRENT_SCHEMA_VERSION` so a bump can't write new-shape files stamped old. 11 tests | `2951ad6` |
| R12 — provider client rebuilt per chat call | `AsyncOpenAI` client created once in `__init__`, reused by chat + chat_stream | `8ec289e` |

## Residuals & watch items

- **Registry DB not chmodded** (`registry/db.py`): never sets owner-only on its SQLite file, unlike backend run data. Left as-is per R8 scope; fix = call `secure_owner_only` equivalent in `Database.connect`.
- **Sandbox: `class` statements in user code always error** (`NameError: '__metaclass__'`) — functional bug in custom_function code paths, untracked. Also watch: `default_guarded_getitem` is literally unrestricted (safe today only because no reachable object supports dunder-string subscripting). Guardrail: never add `getattr`/`type` to the sandbox's `_EXTRA_BUILTINS` — that re-opens the aliased-reference bypass.
- **Sandbox timeout = daemon thread + `join(timeout)`**: a timed-out execution keeps running unkillable. Container/subprocess isolation deferred (would be "Phase 4").
- **R13 watch item — validation/builder rule duplication**: `validation.py` deliberately mirrors builder structural rules (error-edge rules exist in both). Not force-shared; R4's per-handler `validate()` registration is the natural convergence point. Suggested guard test (build+validate every workflow in `samples/`) was **not added** — only registry samples are tested today.
- **RetryConfig**: intentionally deferred; schema surface stays, implement as its own increment.

## Do NOT refactor

- The per-run event loop + per-run `AsyncSqliteSaver` in `runner.py` — deliberate workaround for aiosqlite's loop-binding; "cleaner" shared-saver designs are dead ends.
- Raw SQL over the `writes` table in recovery — brittle-looking but the only way to see pending interrupts without the real graph; keep, with a comment pinning the LangGraph version assumption.
- The writer-thread persistence design in `app/runs/store.py` — synchronous SQLite on the loop self-deadlocks; the queue/thread is correct.
- `Command(resume={id: value})` map-form workaround in `resume_workflow` — LangGraph 1.2.x bug workaround; don't "simplify" it.
- The two-app split (backend vs registry) and the git+SQLite registry design — architecturally sound.
- Introducing a DI framework, an ORM, or microservices — no current pain justifies them.

## Open questions

1. **Sandbox threat model:** is custom_function code ever written by someone other than the workflow owner? R2 proved the RestrictedPython policy holds against escape probes; if untrusted authors are possible, subprocess/container isolation (see residuals) is still warranted.
2. **Multi-process deployment ever planned?** The module-level `RUNS` dict + single writer thread assume one process; if horizontal scaling is on the horizon, the store should target a swappable backend.
