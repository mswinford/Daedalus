# AI Capability Platform — Roadmap

> **Status:** Living top-level plan. Synthesizes the original vision ([`concepts/enterprise-foundry-overview.md`](./concepts/enterprise-foundry-overview.md)) with the architectural reframe settled during design.
> **Component plans:** [AI Forge](./ai-forge-plan.md) · [Capability Registry](./capability-registry-plan.md).

## The goal

Build the **package manager and operating environment for AI capabilities** — discoverable, versioned, reusable, governable units (tools, prompts, skills, agents, workflows, models, …) that applications and agents can find, consume, compose, and trust.

The unit of delivery is the **Capability Package**: manifest + artifact + interface + governance + version + ownership + (eventually) evaluation. The **Capability Manifest** is the central contract everything else builds on.

## Key reframe

The original 7-phase plan treats this as greenfield. In reality, **AI Forge already ships most of the "operating environment"**: workflow authoring + execution (LangGraph), tools, model config, secrets, sandboxing, human-approval gates, and run observability/cost. So we do **not** rebuild a runtime.

The genuinely new work is narrow — **identity, versioning, ownership, lifecycle, search/discovery, and the packaging contract.** That is the registry's real job.

> **Consequence:** the platform is **AI Forge (runtime) + Capability Registry (system-of-record & discovery)**, sharing one `schema` package. The source doc's "Execution Broker / Gateway" are largely already inside AI Forge — they get *extended*, not rebuilt.

## Components

| Component | What it is | Status | Plan |
|---|---|---|---|
| **AI Forge** | Workflow authoring + execution engine (LangGraph); tools, models, secrets, HIL, observability | Shipped (Phase 3) | [ai-forge-plan.md](./ai-forge-plan.md) |
| **Capability Registry** | Identity · versioning · ownership · lifecycle · search · packaging. A thin layer *above* AI Forge; git (provenance) + SQLite→Postgres index | R1 complete (steps 1–8 shipped); R2 in progress — publish-time governance checks, invoke node, run metrics → `evaluation` scores, upgrade automation for existing imports, and live refs (`latest` tracking) shipped | [capability-registry-plan.md](./capability-registry-plan.md) |
| **`schema` package** | Shared Pydantic models — the Capability Manifest contract + all node/tool/workflow types | Shipped; extended with the Capability Manifest (R1 step 1) | in both |

## Everything is a capability

A capability is *any* versioned, shareable, governable unit. The manifest's **`kind`** axis (orthogonal to how it's invoked) covers `tool`, `prompt`, `model_profile`, `skill`, `agent`, `workflow` (core, R1) plus `policy`, `knowledge`, `connector`, `eval_suite` (roadmap). Composites reference other capabilities by `name@version`. Detail in the [registry plan — Capability Kinds](./capability-registry-plan.md#4-capability-kinds).

## Roadmap (three releases)

The original seven phases consolidate into three product releases (as the source doc itself recommends). Each is scoped to what's *actually missing* given the reframe.

### R1 — Find & Reuse *(MVP — complete, steps 1–8 shipped)*
Prove the #1 value: **reuse rate** — do new AI projects consume an existing capability instead of rebuilding?
- ✅ Capability Manifest schema + all core `kind` specs (`schema/capability.py`).
- ✅ Registry service: git-backed store, SQLite index (FTS5), immutable versions, lifecycle state machine.
- ✅ Publish (git commit + index) + search + use APIs; offline CLI (`publish` / `seed`) with eleven sample capabilities in `registry/samples/`.
- ✅ AI Forge **Capabilities** view: browse/search → detail → one-click **Use** (inline import into a target workflow; name-based secret/model remapping), plus per-kind "Use in…" import affordances on agent nodes (`prompt_ref` + `skills[]`).
- **KPI:** % of new workflows that reuse a registered capability.

### R2 — Govern & Compose
From "here's a thing you can use" to "declare it and the platform runs/resolves it."
- ✅ `invoke` node in the AI Forge engine — call a registered capability by `name@version` with mapped I/O; tool kind executes directly, workflow kind expands into the parent graph at build time behind a call frame (per-run version pinning, HIL/resume/recovery work unmodified, parent-side error catch via synthetic error edges). Remote invocation over HTTP still to come.
- ✅ Declared-dependency resolution at publish + automated per-kind breaking-change detection (`registry/publish_checks.py` — refs must resolve with import-time semantics, kind changes across versions rejected, breaking changes require a major bump).
- ✅ Feed real run metrics into `evaluation` scores — provenance stamped on registry imports, per-run capability-usage snapshots, participation-level aggregates (success rate, duration p50/p95, avg cost) pushed to the registry on every terminal run; displayed in the Capabilities view and blended into search ranking.
- ✅ Upgrade automation for existing imports — provenance stamped on all import kinds, "check for updates" with version badges (breaking majors in red), one-click in-place upgrades with per-field drift diff (local edits preserved, workflow references never broken), explicit breaking-change confirmation, and a guard + ack against active/paused runs.
- ✅ Live refs for existing imports — opt-in `latest` tracking per stamped entry (all six kinds): at run start each tracked entry re-resolves to the newest published version within the same major and the run executes that (saved JSON never mutated); breaking majors are not auto-followed — they surface as the red badge → manual upgrade; registry-down or pruned versions fall back to the inlined copy with an info event; per-run pinning (`capability_pins`) keeps pause/resume/restart deterministic.
- Define the `eval_suite` kind (runnable suites / publish gating — Phase 4 territory).
- Graduate SQLite → Postgres (+ pgvector, row-level security).

### R3 — Agent-native
Agents discover and compose capabilities themselves.
- Semantic/intent search (hybrid FTS + vector).
- Agent discovery API (`search` / `describe` / `resolve`).
- MCP adapter (expose capabilities as MCP servers + an MCP node in AI Forge).
- Enforcement gateway: ACL, credential brokerage, audit — built on the governance metadata recorded since R1.

## Mapping to the original phases

| Original phase | Release |
|---|---|
| 0 Contract · 1 Registry | **R1** Find & Reuse |
| 2 Runtime · 3 Governance · 4 Evaluation | **R2** Govern & Compose |
| 5 Agentic discovery · 6 Skills & Workflows · 7 Federated | **R3** Agent-native |

## Guiding principles
1. **Reuse before rebuild.** Discover before you create.
2. **Capabilities over applications.** Optimize for reusable components, not disconnected apps.
3. **Declarative over procedural.** Declare `requires: [...]`; the platform resolves.
4. **Runtime is an implementation detail.** API / MCP / container / local — consumers don't care.
5. **Centralize control, federate ownership.** (later)
6. **Security travels with the capability.** Permissions, classification, approval, owner declared in the manifest from day one.
7. **Evaluation is part of packaging.** Production capabilities ship with evidence they work.

## Documentation map
- `docs/ROADMAP.md` — this file (platform-level).
- `docs/ai-forge-plan.md` — AI Forge application plan & status.
- `docs/capability-registry-plan.md` — registry component plan (R1 complete; R2 in progress — publish-time governance checks, invoke node, run metrics → `evaluation` scores, upgrade automation for existing imports, and live refs shipped).
- `docs/data-flow.md` — engine data-flow reference.
- `docs/concepts/` — original vision docs: `enterprise-foundry-overview.md`, `capabilities.md`, `capability-registry.md`.
