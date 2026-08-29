# Graph Report - ai-forge  (2026-08-29)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 691 nodes · 1464 edges · 38 communities (35 shown, 3 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 79 edges (avg confidence: 0.93)
- Token cost: 4,683 input · 4,866 output

## Graph Freshness
- Built from commit: `549c3ca0`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- models.py
- Workflow
- dependencies
- GraphBuilder
- builder.py
- test_tools.py
- test_conditions.py
- test_secrets.py
- Backend API Layer (FastAPI Routers)
- test_runs_api.py
- runs.py
- compilerOptions
- _execute
- ConfigPanel.tsx
- run_sandboxed
- WorkflowEditor.tsx
- api.ts
- workflowTypes.ts
- api/secrets.py
- RunPanel.tsx
- ToolsPanel.tsx
- App.tsx
- get_settings
- WorkflowStore
- ModelConfig
- secret_file
- get_run
- conftest.py
- AI Forge Project
- ai-forge

## God Nodes (most connected - your core abstractions)
1. `GraphBuilder` - 46 edges
2. `Workflow` - 45 edges
3. `run_workflow_sync()` - 43 edges
4. `Node` - 38 edges
5. `Edge` - 29 edges
6. `StateFieldType` - 25 edges
7. `validate_workflow()` - 23 edges
8. `ToolDefinition` - 18 edges
9. `evaluate_condition()` - 18 edges
10. `compilerOptions` - 18 edges

## Surprising Connections (you probably didn't know these)
- `Tool Schema and Execution` --semantically_similar_to--> `Tool Execution Boundary`  [INFERRED] [semantically similar]
  docs/data-flow.md → PLAN.md
- `GraphBuilder` --uses--> `AgentNodeConfig`  [INFERRED]
  backend/app/engine/builder.py → schema/models.py
- `evaluate_condition()` --uses--> `ConditionType`  [INFERRED]
  backend/app/engine/conditions.py → schema/models.py
- `GraphBuilder` --uses--> `CustomFunctionNodeConfig`  [INFERRED]
  backend/app/engine/builder.py → schema/models.py
- `GraphBuilder` --uses--> `HumanInLoopNodeConfig`  [INFERRED]
  backend/app/engine/builder.py → schema/models.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Tool Execution Sandbox Boundary** — plan_tool_execution, agents_backend_app_sandbox, plan_secrets_store, plan_node_agent, docs_data_flow_tools [EXTRACTED 0.90]
- **Workflow State Channel System** — plan_workflow_state, plan_node_agent, plan_node_transform, plan_node_custom_function, plan_per_agent_isolation [EXTRACTED 0.95]
- **Async Run Execution Pipeline** — plan_async_execution, agents_backend_app_api, plan_langgraph, readme_rest_api, plan_node_human_in_loop [INFERRED 0.85]

## Communities (38 total, 3 thin omitted)

### Community 0 - "models.py"
Cohesion: 0.06
Nodes (73): _build_initial_state(), _extract_result(), Any, RunEvent, Workflow, Execute workflows using LangGraph., Resume a paused workflow with human-provided input. Rebuilds the same graph…, Validate run input against workflow.state_schema (if defined). (+65 more)

### Community 1 - "Workflow"
Cohesion: 0.10
Nodes (53): _find_cycle(), BaseModel, Workflow, Static validation of a workflow definition (no execution, no LLM calls).…, Return one cycle (list of node ids) if the graph has a directed cycle., validate_workflow(), ValidationIssue, ValidationResult (+45 more)

### Community 2 - "dependencies"
Cohesion: 0.04
Nodes (47): autoprefixer, axios, clsx, allowScripts, esbuild@0.21.5, dependencies, axios, clsx (+39 more)

### Community 3 - "GraphBuilder"
Cohesion: 0.06
Nodes (26): AgentState, GraphBuilder, Any, RunEvent, Workflow, Sum per-model token usage against each model's pricing (per 1M tokens)., Accumulate token usage and emit an llm_call trace event., Wrap a node function to emit node_start/node_end (or node_error) with timing. (+18 more)

### Community 4 - "builder.py"
Cohesion: 0.09
Nodes (25): ABC, Translate workflow JSON into a LangGraph StateGraph., create_provider(), LLMProvider, LLMResult, Message, OpenAICompatibleProvider, Any (+17 more)

### Community 5 - "test_tools.py"
Cohesion: 0.13
Nodes (33): build_tool_schema(), _builtin_echo(), execute_tool(), Any, Tool schema building and execution for agent tool-calling., Render a template string. ``${NAME}`` placeholders are filled from the process…, Convert a ToolDefinition to OpenAI tool-calling format., Decorator to register a builtin tool handler. (+25 more)

### Community 6 - "test_conditions.py"
Cohesion: 0.12
Nodes (33): Replace {{path}} placeholders with values resolved from state. Paths are dot-…, _render_template(), _coerce_literal(), ConditionError, _eval_comparison(), evaluate_condition(), Any, Evaluate ConditionConfig expressions against workflow state. (+25 more)

### Community 7 - "test_secrets.py"
Cohesion: 0.12
Nodes (24): delete_secret(), get_secret(), list_secrets(), load_secrets(), Secrets storage: ~/.ai-forge/secrets.json with env-var precedence. Resolution…, Read all secrets from the file. Returns {} if the file doesn't exist., Resolve a secret by name: env var first, then file. Returns None if unset., Upsert a secret in the file (does not touch env vars). (+16 more)

### Community 8 - "Backend API Layer (FastAPI Routers)"
Cohesion: 0.08
Nodes (29): Backend API Layer (FastAPI Routers), Backend Engine (LangGraph Builder/Runner), Backend Sandbox (RestrictedPython), Frontend Flow Components (React Flow), Frontend Library (API Client, Types, Graph Transforms), Schema Models (Pydantic), Condition Expressions (json_path, regex), Input Mapping (Reserved vs Data) (+21 more)

### Community 9 - "test_runs_api.py"
Cohesion: 0.10
Nodes (17): _clear_runs(), client(), hil_client(), fixture, Tests for the async run API: POST 202, GET polling, and WebSocket streaming., Client with a workflow containing a human_in_loop node., A run hitting a human_in_loop node pauses with status='paused'., Resuming a paused run with input completes the workflow. (+9 more)

### Community 10 - "runs.py"
Cohesion: 0.13
Nodes (22): Run execution API: async kickoff, WebSocket event streaming, and retrieval.…, create_workflow(), delete_workflow(), get_workflow(), list_workflows(), _load_workflow(), delete, get (+14 more)

### Community 11 - "compilerOptions"
Cohesion: 0.08
Nodes (23): compilerOptions, allowImportingTsExtensions, baseUrl, isolatedModules, jsx, lib, module, moduleDetection (+15 more)

### Community 12 - "_execute"
Cohesion: 0.12
Nodes (21): _execute(), _is_terminal(), _prune_runs(), Any, post, RunEvent, Resume a paused run with human input (streaming events)., Kick off a run in the background and return its id for streaming. (+13 more)

### Community 13 - "ConfigPanel.tsx"
Cohesion: 0.10
Nodes (11): ConfigPanel(), Props, AgentNodeConfig, CustomFunctionNodeConfig, EndNodeConfig, FieldMapping, HumanInLoopNodeConfig, HumanInputField (+3 more)

### Community 14 - "run_sandboxed"
Cohesion: 0.17
Nodes (19): _build_namespace(), _execute(), _get_secret(), _inplacevar_(), Any, Sandboxed execution of custom Python functions., Resolve a secret by name (env > file). Raises if not found., Execute Python code in a RestrictedPython sandbox. Returns the ``result`` dict… (+11 more)

### Community 15 - "WorkflowEditor.tsx"
Cohesion: 0.22
Nodes (17): FlowNode(), ICONS, subtitle(), streamRunEvents(), edgesToRF(), FlowNodeData, nodesToRF(), rfToEdges() (+9 more)

### Community 16 - "api.ts"
Cohesion: 0.13
Nodes (15): Props, SecretsPanel(), api, HumanInterruptValue, RunEventType, RunStartResponse, SecretInfo, secretsApi (+7 more)

### Community 17 - "workflowTypes.ts"
Cohesion: 0.20
Nodes (14): AgentNode, BaseNode, ConditionalNode, ConditionConfig, ConditionType, CustomFunctionNode, EdgeType, EndNode (+6 more)

### Community 18 - "api/secrets.py"
Cohesion: 0.17
Nodes (12): BaseModel, delete, get, put, Secrets management API: list, upsert, delete., List configured secret names (values are never returned)., Create or update a secret., Delete a secret from the file. (+4 more)

### Community 19 - "RunPanel.tsx"
Cohesion: 0.22
Nodes (11): fmtMs(), formatOutput(), NodeExecution, RunPanel(), RunPanelProps, summarize(), HumanInterruptField, RunEvent (+3 more)

### Community 20 - "ToolsPanel.tsx"
Cohesion: 0.21
Nodes (12): HeaderRow, IMPL_LABEL, Props, rowsToParams(), toHeaderRows(), ToolForm(), ToolsPanel(), toRows() (+4 more)

### Community 21 - "App.tsx"
Cohesion: 0.24
Nodes (6): App(), AppLayout(), WorkflowSidebar(), queryClient, EmptyState(), WorkflowEditor()

### Community 22 - "get_settings"
Cohesion: 0.27
Nodes (6): get_settings(), BaseModel, Settings, JSON file-based persistence for workflows., main(), CLI entry point for AI Forge.

### Community 23 - "WorkflowStore"
Cohesion: 0.22
Nodes (5): Workflow, File-based workflow storage., List all workflows (metadata only)., Get a workflow by ID., WorkflowStore

### Community 24 - "ModelConfig"
Cohesion: 0.50
Nodes (3): ModelsPanel(), Props, ModelConfig

### Community 25 - "secret_file"
Cohesion: 0.50
Nodes (4): client(), fixture, Redirect all secrets I/O to a temp file and clear env pollution., secret_file()

### Community 26 - "get_run"
Cohesion: 0.67
Nodes (3): get_run(), get, Fetch a run's current state (in-memory; gone after a process restart).

## Knowledge Gaps
- **82 isolated node(s):** `Props`, `HumanInterruptValue`, `RunEventType`, `RunStartResponse`, `ValidationIssue` (+77 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `GraphBuilder` connect `GraphBuilder` to `models.py`, `Workflow`, `builder.py`, `test_tools.py`, `test_conditions.py`?**
  _High betweenness centrality (0.074) - this node is a cross-community bridge._
- **Why does `Workflow` connect `Workflow` to `models.py`, `GraphBuilder`, `builder.py`, `test_tools.py`, `runs.py`, `get_settings`, `WorkflowStore`?**
  _High betweenness centrality (0.073) - this node is a cross-community bridge._
- **Why does `run_workflow_sync()` connect `models.py` to `Workflow`, `GraphBuilder`, `builder.py`, `test_tools.py`, `runs.py`, `_execute`?**
  _High betweenness centrality (0.039) - this node is a cross-community bridge._
- **Are the 13 inferred relationships involving `GraphBuilder` (e.g. with `ConditionError` and `LLMProvider`) actually correct?**
  _`GraphBuilder` has 13 INFERRED edges - model-reasoned connections that need verification._
- **Are the 11 inferred relationships involving `Workflow` (e.g. with `create_workflow()` and `_load_workflow()`) actually correct?**
  _`Workflow` has 11 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `run_workflow_sync()` (e.g. with `_execute()` and `RunEvent`) actually correct?**
  _`run_workflow_sync()` has 3 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Props`, `HumanInterruptValue`, `RunEventType` to the rest of the system?**
  _82 weakly-connected nodes found - possible documentation gaps or missing edges._