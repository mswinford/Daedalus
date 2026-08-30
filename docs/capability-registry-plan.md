# Capability Registry — Implementation Plan (Draft)

> **Status:** Component plan — decisions settled, R1 build-ready. Part of the [platform roadmap](./ROADMAP.md); a thin layer above AI Forge.
> **Scope:** R1 (Find & Reuse) is build-ready; R2/R3 are roadmap.
> **Companion docs:** [Roadmap](./ROADMAP.md) · [AI Forge plan](./ai-forge-plan.md) · concepts: `concepts/enterprise-foundry-overview.md`, `concepts/capabilities.md`, `concepts/capability-registry.md`.

## 1. Context & reframe

The overview describes a 7-phase "enterprise operating environment for AI capabilities." A large fraction of it **already exists in AI Forge**. The registry's genuinely new value is narrow; the rest is reuse:

| Platform concept (docs) | Already in AI Forge | New work |
|---|---|---|
| Capability (executable unit) | **Workflow** = input→graph→output, async, checkpointed | Wrap in a manifest |
| Tool | `ToolDefinition` (`builtin`/`custom_function`/`http`) | — |
| Model / runtime config | `ModelConfig`, OpenAI-compatible provider | — |
| Secrets store | `~/.ai-forge/secrets.json` + env precedence | Shared credential *refs* in manifest |
| Sandboxing | RestrictedPython (containers deferred) | Containers later |
| Human approval gate | HIL nodes + Pending Approvals sidebar | Map to `human_approval` metadata |
| Observability / cost | Run events, token + cost tracking | Feed "quality metrics" |
| **Identity · versioning · ownership · lifecycle · search/discovery · packaging contract** | ❌ none | **← the registry's real job** |

**Reframe:** the registry is a thin *system-of-record + discovery* layer above AI Forge — **not a new runtime.** The docs' "AI Runtime / Execution Broker / Gateway" are largely already shipped inside AI Forge and should not be rebuilt.

## 2. Key decisions (settled)

| # | Decision | Choice |
|---|---|---|
| 1 | Capability ↔ Workflow | **A capability is any shareable unit; a workflow is one `kind`.** The manifest wraps the artifact (embeds or references it). AI Forge stays the authoring/runtime tool; registry sits above it. |
| 2 | Storage (enterprise) | **Git (provenance/publish/review) + Postgres (serving/query/ACL); API serves only from the DB, git off the read path.** R1 runs the DB on **SQLite** — same schema/queries, single file — so "add the DB later" = swap engine + enable pgvector/RLS, *not* a rewrite. |
| 3 | Native interface | **`ai_forge_workflow` first**; manifest `interface.type` is protocol-agnostic (`ai_forge_workflow \| mcp \| http`). MCP + others are roadmap adapters. |
| 4 | Governance in R1 | **Metadata + lifecycle state machine only** (no IAM/policy/DLP enforcement). Enforcement → later gateway. |
| 5 | Marketplace portal | **Inside the existing AI Forge React SPA** (browse/search/use view), talking to both backends. |
| 6 | Versioning | **Strict semver** + `stage`; `latest` = newest PUBLISHED; per-kind breaking rules are documented guidance, enforced by the R2 resolver. |
| 7 | Packaging (R1) | **`registry` as a 4th package in the existing wheel** + an `ai-forge-registry` console script (same pattern as `ai_forge`/`app`/`schema`). |

## 3. Concept mapping & pushbacks

- **Everything is a capability.** A capability is *any* versioned, shareable, governable unit of AI capability — not just workflows. Tools, prompts, model profiles, skills, agents and workflows are all capabilities; the registry manages them uniformly (one search, one versioning, one governance).
- **`kind` is a first-class axis.** The manifest carries a `kind` (what it *is*) orthogonal to how it's *invoked*. See §4 Capability Kinds.
- **MCP is an assumption, not a requirement.** AI Forge doesn't speak MCP (own tool impls + REST `POST /run`). Keep it one of several interface types.

**Challenges to the source docs:**
1. **Scale mismatch** — IAM/policy/DLP/federated multi-tenant assume many teams on a shared deployment; AI Forge is single-user/local-first today. Record governance as metadata now, enforce later.
2. **Dependency resolver = defer** — active semver-range resolution (mini-npm) is high-complexity/low-MVP-value. Start with *declared dependencies as metadata* + existence validation; resolve actively in R2 (now that composites reference each other).
3. **MCP-first is premature** — it blocks the cheap file-import reuse flow.

## 4. Capability Kinds

A capability's **`kind`** says what it *is* (semantic); its **`interface`** says how it's *invoked* (protocol + I/O contract). The two are orthogonal: a `tool` kind can ship as inline code, a `builtin`, or an MCP tool; a `workflow` kind ships as workflow JSON.

Kinds split into **leaf** (atomic) and **composite** (reference other capabilities by `name@version`). **Core** kinds map to a real AI Forge primitive and have a reuse path today; **roadmap** kinds are sketched but not built in R1.

| Kind | Leaf/Comp | Maps to in AI Forge | How a consumer uses it | Tier |
|---|---|---|---|---|
| **tool** | leaf | `ToolDefinition` | agent calls it; add to workflow `tools[]` | core |
| **prompt** | leaf | inline `system_prompt` strings | set an agent's prompt / template ref | core |
| **model_profile** | leaf | `ModelConfig` | add to workflow `models[]`; fills `models_required` | core |
| **skill** | composite | instructions + tool refs (no model, no graph) | attached to an agent node; folded into its prompt+tools at runtime | core (light) |
| **agent** | composite | model_profile + prompt + tools + skills (single unit, no graph) | import as an agent node / invoke node (R2) | core (light) |
| **workflow** | composite | `Workflow` graph — the only kind that carries a graph | run end-to-end / embed as sub-graph | core (default today) |
| **policy** (guardrail) | leaf | new; ties to HIL + secrets | applied at runtime to any capability | roadmap |
| **knowledge** | leaf | new; RAG corpus + retrieval iface | retrieval node / agent grounding | roadmap |
| **connector** | composite | auth + tools bundle for an external system | referenced by tools/workflows | roadmap |
| **eval_suite** | leaf | new; test/eval bundle | CI quality gate; scores capabilities | roadmap |

### Per-kind `spec` (a union discriminated by `kind`)

Shared reference type used by composites:
```python
class CapabilityRef(BaseModel):
    name: str                    # owner/name
    version: str = "latest"      # semver or "latest"
```

Core kinds:
```python
class ToolSpec(BaseModel):          # kind=tool
    tool: ToolDefinition           # reuse existing (name/description/parameters/impl)
class PromptSpec(BaseModel):        # kind=prompt
    text: str                      # template with {{var}} placeholders
    variables: list[str] = []
    role: Literal["system","user","assistant"] = "system"
class ModelProfileSpec(BaseModel):  # kind=model_profile
    model: ModelConfig             # reuse existing
    notes: Optional[str] = None
class SkillSpec(BaseModel):         # kind=skill (composite) — instructions + tools, no model, no graph
    prompt: Optional[str] = None
    prompt_ref: Optional[CapabilityRef] = None   # or reference a prompt capability
    tools: list[CapabilityRef] = []              # tool capabilities it uses
class AgentSpec(BaseModel):         # kind=agent (composite) — single self-directed unit, NO embedded graph
    model_profile: CapabilityRef                   # a model_profile capability
    prompt: Optional[str] = None
    prompt_ref: Optional[CapabilityRef] = None
    tools: list[CapabilityRef] = []
    skills: list[CapabilityRef] = []               # folded into the agent at runtime (below)
class WorkflowSpec(BaseModel):      # kind=workflow — the only kind that carries a graph
    workflow: Optional[Workflow]
    workflow_ref: Optional[str] = None

KindSpec = Union[ToolSpec, PromptSpec, ModelProfileSpec, SkillSpec, AgentSpec, WorkflowSpec]
```

Roadmap kinds (sketched, not built in R1): `PolicySpec{type, config}` · `KnowledgeSpec{source, query_interface}` · `ConnectorSpec{system, auth, tools[]}` · `EvalSuiteSpec{cases[]}`.

**Interface contracts are kind-aware** — the universal `interface` block takes a natural shape per kind: tool = function signature (from `tool.parameters`); prompt = declared variables; model_profile = messages→completions; workflow = `state_schema` in/out; knowledge = `query(text, top_k)→chunks`.

### Runtime semantics (settled)
- **skill → agent node.** A skill is *not* a graph node; it's a `skills[]` field on an agent node. At runtime the engine folds each skill into that agent's effective config — concatenates its prompt into the system prompt and unions its tools. **Always-active in R1** (no dynamic/just-in-time loading — that's R3).
- **Only `workflow` carries a graph.** An `agent` or `skill` that needs a real multi-step graph should be published as a `workflow` kind instead — keeps the kinds non-overlapping.
- **Import = inline (R1).** Composites declare their refs by `name@version` in the manifest, but "Use" *inlines* the referenced capabilities' contents into the imported artifact (self-contained). Live refs that resolve at runtime arrive with the R2 invoke node.

### Versioning & breaking changes (per kind)
- **Versioning:** strict semver (`MAJOR.MINOR.PATCH`); publish state is `stage`; prereleases use a semver `-tag` (e.g. `1.0.0-rc.1`). No separate channel/track system in R1.
- **`latest`:** resolves to the newest **PUBLISHED** version of a name (steering away from DEPRECATED). Versions are immutable; an imported artifact is a *snapshot* — it does not track its source, so "upgrade" = re-import a newer version. Live upgrade tracking arrives in R2.
- **Kind stability:** a capability cannot change `kind` across versions — that's a new capability (new name), since the consumer contract differs.
- **Breaking rules** (documented guidance + an optional `breaking_changes` note; no machine checker until the R2 resolver). *Breaking* = anything that changes what a **consumer** must do to keep working:
  - *tool* — JSON-Schema backward-compat on input `parameters` + output shape (rename/remove/retype a param or change output = major; add optional param / improve description = minor).
  - *workflow* — compat of the declared `state_schema` in/out (internal graph edits = minor).
  - *model_profile* — swapping the underlying model id is **major** (silently changes what you get); temperature/param tweaks = minor.
  - *prompt / skill* — soft contract: structural or ref changes (adding a required variable/tool, changing referenced capabilities) = major; text edits = minor (consumers opt in by bumping).

## 5. Architecture

### 5.1 Shared `schema` package — the Capability Manifest (linchpin)
New Pydantic models in `schema/`, installed by both apps (single source of truth). Carries a `kind` (what it is) + kind-specific `spec`; protocol-agnostic via `interface`; phase-growable.

```python
class CapabilityKind(str, Enum):
    TOOL="tool"; PROMPT="prompt"; MODEL_PROFILE="model_profile"
    POLICY="policy"; SKILL="skill"; AGENT="agent"; WORKFLOW="workflow"
    KNOWLEDGE="knowledge"; CONNECTOR="connector"; EVAL_SUITE="eval_suite"

class InterfaceType(str, Enum):  AI_FORGE_WORKFLOW / MCP / HTTP   # workflow-native now; container later
class LifecycleStage(str, Enum): DRAFT / REVIEW / APPROVED / PUBLISHED / DEPRECATED / RETIRED
class SecurityStatus(str, Enum): UNREVIEWED / IN_REVIEW / APPROVED / FLAGGED
class DataClassification(str, Enum): PUBLIC / INTERNAL / CONFIDENTIAL / RESTRICTED

class CapabilityInterface(BaseModel):
    type: InterfaceType
    input_schema: dict                # JSON Schema (kind-aware shape)
    output_schema: dict
    invocation: dict                  # protocol-specific call details

class CapabilityGovernance(BaseModel):   # METADATA ONLY in R1 — no enforcement
    owner: str                        # required (e.g. "finance")
    data_classification: DataClassification = INTERNAL
    human_approval_required: bool = False     # maps to AI Forge's HIL gate
    security_status: SecurityStatus = UNREVIEWED
    allowed_consumers: list[str] = []         # ACL metadata; enforced in R3

class CapabilitySemantics(BaseModel):  # for agent discovery (R3)
    purpose: Optional[str]; use_when: list[str] = []; avoid_when: list[str] = []
    related: list[str] = []

class CapabilityEvaluationRef(BaseModel):  # scores computed by runtime (R2)
    suite_id: Optional[str]; last_scored_at: Optional[float]; score: Optional[float]

class CapabilityManifest(BaseModel):
    # Identity
    name: str                         # "owner/name", e.g. "finance/invoice-analyzer"
    version: str                      # semver "1.2.0" — versions are immutable
    description: str
    tags: list[str] = []
    # What it is + its payload
    kind: CapabilityKind = Workflow
    spec: KindSpec                    # discriminated union on kind (see §4)
    interface: Optional[CapabilityInterface] = None   # required for tool & workflow; others inherit from spec until invokable (R2)
    # Composition (metadata only in R1; resolved in R2)
    dependencies: list[CapabilityRef] = []   # other capabilities by name@version
    models_required: list[str] = []
    secrets_required: list[str] = []
    governance: CapabilityGovernance
    stage: LifecycleStage = DRAFT
    created_at: float; updated_at: Optional[float]; published_at: Optional[float]
    source_repo: Optional[str]; source_commit: Optional[str]   # provenance (pipeline)
    semantics: Optional[CapabilitySemantics] = None            # agent discovery (R3)
    evaluation: Optional[CapabilityEvaluationRef] = None       # scores (R2)
```

`owner/name` encodes ownership into the ID (npm-scoped style). Identity + `kind` + `spec` are required; `interface` is required for `tool` & `workflow` kinds (others inherit their contract from `spec` until they become invokable in R2); governance is metadata; semantics/evaluation are optional and light up in later releases. The `spec` union is Pydantic-discriminated by `kind`.

### 5.2 Registry service — new `registry/` package (same stack)
```
registry/
  config.py      # Settings: registry_db (SQLite→Postgres), capabilities_repo (git path)
  db.py          # connection + schema init (portable SQL; FTS5 now, pgvector later)
  store.py       # immutable version rows + lifecycle transitions
  indexer.py     # git → DB sync: parse manifests, upsert versions, build FTS index
  search.py      # keyword/FTS now; pluggable vector backend later
  main.py        # FastAPI app + lifespan (open DB, sync-on-start)
  api/           # capabilities.py, search.py, publish.py, use.py
```
DB: `capability_versions(name, version, kind, manifest_json, artifact_json, stage, security_status, source_commit, created_at, PK(name,version))` — **immutable** — plus an FTS5 table over name/description/tags.

**Publish (R1, hybrid):** `POST /capabilities` writes the capability dir into the local git repo + commits, then runs `indexer.sync()`. You get the git-artifact model immediately *and* can drive it from the UI. Enterprise deployments later switch to pure PR-driven with the same indexer.

### 5.3 Storage model
Git = provenance/publish/review (immutable history, PR security review, audit, rollback). Postgres/SQLite = serving/query/ACL (live index; RLS + pgvector at scale). **API never touches git on reads.** Publish: author → PR → CI validates manifest/deps → merge → sync imports an immutable version row + rebuilds FTS.

### 5.4 API surface
```
GET    /registry/capabilities                  # list (filter stage/tags/owner/kind)
GET    /registry/capabilities/{name}           # latest (newest PUBLISHED)
GET    /registry/capabilities/{name}/versions
GET    /registry/capabilities/{name}/{version} # immutable
POST   /registry/capabilities                  # publish (git commit + index)
POST   /registry/capabilities/{name}/lifecycle # draft→review→approved→published…
GET    /registry/search?q=&tags=&stage=&kind=  # FTS now, vector later
GET    /registry/capabilities/{n}/{v}/artifact # download payload → "Use"
```

### 5.5 AI Forge integration (minimal in R1)
- **Frontend:** a "Capabilities" view in the existing SPA (sidebar/top-bar entry). Browse/search (filter by kind) → detail (description, owner, stage, tags, I/O schema, semantics) → **"Use capability"** → fetch payload → import per kind. Reuses React Query + components. Vite proxy gains a second entry (`/registry` → registry port, e.g. :3100).
- **One-click "Use"** into a chosen target workflow — no bespoke per-kind editors; the user lands in the existing editor to tweak. Per-kind effect (all inline): `workflow` → save as a new workflow file (existing `POST /api/workflows`); `tool` → add to the workflow's `tools[]`; `model_profile` → add to `models[]`; `prompt` → set an agent's prompt via a *prompt-ref*; `skill` → add to an agent node's `skills[]` (folded into its prompt+tools at runtime); `agent` → import as a single agent node (no graph).
- **Ref remapping on import (resolved once):** secrets map by exact name to the consumer's global store — a missing one is flagged for the user to supply; model refs are inlined into the target workflow's `models[]`, suffixed on id clash. Because imports inline, there's no ongoing binding to maintain.
- **AI Forge backend:** mostly the existing workflow-create endpoint; plus small affordances — *prompt-ref* on agents, `skills[]` on agent nodes with runtime fold-in, and *agent-as-node*.

## 6. Phased build plan

**R1 — Find & Reuse (MVP; proves the #1 KPI, reuse rate):**
1. `schema/capability.py` manifest models + all core kind specs + extend `scripts/generate_schema.py` → `capability_schema.json`; validation tests.
2. Registry skeleton: `config.py`, `db.py` (SQLite + schema), `main.py` + `/health`.
3. `store.py` + `indexer.py` (git→DB, FTS5); tests against a temp git repo.
4. API: capabilities / search / publish / use; TestClient tests.
5. Publish mechanism (git commit + sync).
6. Frontend Capabilities view (filter by kind) + per-kind "Use" import; `tsc --noEmit` + build.
7. AI Forge import affordances — *prompt-ref* on agents, `skills[]` on agent nodes with runtime fold-in, *agent-as-node*.
8. Run both servers together (dev script / docker-compose); update README/PLAN.

**R2 — Govern & Compose (roadmap):** new `capability`/`invoke` node in the AI Forge engine (call a registered capability by `name@version`, map I/O, stream sub-run events) · remote invocation over HTTP · declared-dependency resolution at publish + automated per-kind breaking-change detection · feed real run metrics (cost/success/latency) into `evaluation` scores · live refs + upgrade automation for existing imports · graduate SQLite→Postgres + pgvector.

**R3 — Agent-native (roadmap):** semantic/intent search · agent discovery API (`search`/`describe`/`resolve`) · MCP adapter (expose capabilities as MCP servers + an MCP node in AI Forge) · enforcement gateway (ACL/credential-brokerage/audit) built on the recorded metadata.

## 7. R1 scope decisions & deferred items

**Settled for R1:**
- **Interface required only for `tool` & `workflow`;** other kinds inherit their contract from `spec` until they become invokable (R2).
- **One-click inline import** into a target workflow; name-based secret mapping + model inlining (no namespacing system, no ongoing binding).
- **Imports are snapshots** — re-import to upgrade (`latest` = newest PUBLISHED).
- **Versioning = strict semver + `stage`;** per-kind breaking rules are documented guidance (+ optional `breaking_changes` note), enforced by the R2 resolver.
- **Packaging:** `registry` ships as a 4th package in the existing wheel + an `ai-forge-registry` console script (same pattern as `ai_forge`/`app`/`schema`).

**Deferred to R2/R3 (direction only, no R1 code):**
- **Search ranking:** FTS5 keyword now; hybrid FTS+vector + embedding-model choice in R3.
- **Topology/auth:** one registry : one AI Forge on localhost for R1; shared central registry + inter-service auth later.
- **Evaluation:** `evaluation` stays optional metadata (unpopulated); suite format defined when the `eval_suite` kind lands.
- **Compatibility checker:** automated per-kind breaking-change detection, alongside the dependency resolver (R2).
- **Live refs & upgrade automation:** runtime-resolved references + re-pointing existing imports to newer versions (R2).
