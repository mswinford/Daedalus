# Data Flow Reference

How a workflow run flows from HTTP request to response, and what each node type reads and writes. All references are to the current engine code.

---

## 1. The big picture

```
POST /api/workflows/{id}/run  {"request": "...", "score": 95, ...}
        │
        ▼
_load_workflow()          loads ~/.ai-forge/workflows/{id}.json, validates against Pydantic schema
        │
        ▼
run_workflow_sync()       backend/app/engine/runner.py:35
        │  1. validate input against state_schema (if defined)
        │  2. build LangGraph graph from workflow JSON (GraphBuilder)
        │  3. map run input → initial state
        │  4. execute graph to completion (blocking)
        ▼
response: WorkflowRun { status, output_data: {output, messages, data, node_outputs}, error?, events }
```

The workflow JSON is translated into a LangGraph `StateGraph` once per run (`backend/app/engine/builder.py`). Every node is an async function that receives the shared state and returns the parts of the state it changed.

## 2. The shared state

Every node reads and writes one state object with five channels (`builder.py:38-43`):

| Channel | Type | Purpose |
|---|---|---|
| `messages` | list | Chat history. Agents append to it; a later agent sees earlier agents' conversation. |
| `output` | str | The most recent node's primary output string. Overwritten by each node. This is what regex conditions match against and what `{{output}}` templates render. |
| `error` | str | Reserved. Currently no node writes it; failures raise exceptions instead and surface as a failed run. |
| `data` | dict | The general-purpose data bag. Run inputs land here; nodes add fields here for downstream use. Addressable as `$.data.<field>` in conditions/templates. |
| `_node_outputs` | dict | Per-node results, keyed by node id. In the HTTP response this is returned as `node_outputs`. Not addressable by conditions (use `data`), but useful for debugging and templates (`{{_node_outputs.grade.label}}`). |

### Input mapping (runner.py:48-63)

Run input fields are split into two groups:

- **Reserved keys** (`messages`, `output`, `error`, `data`, `_node_outputs`) map directly onto the state channel of the same name — i.e. they *override* the default.
- **Everything else** is collected under `data`: input `{"score": 95, "text": "hi"}` → `state["data"] = {"score": 95, "text": "hi"}`.

So any field you pass in a run body is available as `$.data.<field>` to conditions, templates, sandbox code, and agents.

## 3. Node types: what each one reads and writes

| Node | Reads | Writes | Routing after it |
|---|---|---|---|
| `start` | — (not a real function) | — | Static edge(s); entry point of the graph |
| `end` | — | — | Terminal (LangGraph `END`) |
| `agent` | `messages`, `data` (if no conversation yet), model config, tools | `messages`, `output`, `_node_outputs[id]` | Static or conditional edges |
| `conditional` | — (passthrough function) | nothing | **Router**: conditions decide which branch edge |
| `transform` | state via template paths / field mappings / referenced sandbox code | `output`, `data[output_field]`, `_node_outputs[id]` | Static or conditional edges |
| `custom_function` | full `state` (sandbox variable) | `output`, `data[<declared output_fields>]`, `_node_outputs[id]` | Static or conditional edges |
| `human_in_loop` | — | — | Not implemented (`NotImplementedError`, Phase 3) |

### start / end

- `start` is not added to the graph as a node; its outgoing edges are wired from LangGraph's `START` (`builder.py:334`). If the workflow has no start node, the first non-end node becomes the entry point (`builder.py:352-357`).
- Edges pointing at an `end` node are rewired to LangGraph's `END` (`builder.py:305, 349`). The end node's `output_fields` config is declarative only — it does not filter the response.

### agent (builder.py:99-171)

1. Resolves its LLM provider from `workflow.models` via `config.model_id` (fails with `ValueError` if missing).
2. Builds the prompt: `[system message from config.system_prompt] + state["messages"]`.
3. **Input injection:** if there is no `user` or `assistant` message in that list yet, it appends one user message containing the **entire `data` dict as JSON** (`builder.py:120-123`). Empty `data` → the literal string `"Begin."`. There is no per-field selection — the LLM sees all of `data`.
4. **Tool loop** (up to `config.max_iterations`, default 10): calls the provider; if the response has no `tool_calls`, it's final and the loop breaks. Otherwise each tool call is executed (see [Tools](#tools)) and the results are appended as `tool` messages before the next LLM call.
5. Writes back:
   - `messages` = previous messages + final assistant message
   - `output` = final content string
   - `_node_outputs[node.id]` = `{"content": final_content}`

**Gotcha — conversation continuity:** because agents share `state["messages"]`, a *second* agent node in the same run sees the first agent's conversation and will **not** re-inject `data` as a user message (the "no user/assistant message" check at step 3 fails). If you need two independent agents, that's currently not supported — the second one continues the first one's conversation.

### conditional (builder.py:173-177, 286-318)

The node function itself is a **passthrough** (`return state`). All logic lives in the router built for its outgoing edges:

- `config.conditions[i]` is evaluated against the current state, in order.
- The first condition that matches routes to the **i-th non-`default` outgoing edge** of this node (in workflow edge order).
- If none match, it takes `config.default_branch`'s handle if set, else the `"default"` handle.
- If nothing matches and there is no default, the run fails with `ConditionError`.

### transform (builder.py:179-231)

Three modes, selected by `config.mode`; all write to `data[config.output_field]` and `output`:

| Mode | Behavior |
|---|---|
| `template` | Renders `config.template`, replacing `{{path}}` placeholders. Paths are dot-separated, resolved against the **whole state** (`{{output}}`, `{{data.score}}`, `{{_node_outputs.grade.label}}`). Missing paths render as empty string (`builder.py:25-34`). |
| `mapping` | For each `field_mappings` entry, resolves `source` (same path syntax) and puts it under `target`; missing → `""`. The resulting dict is stringified (`str(...)`) into `output` — note this is Python repr, not JSON. |
| `custom_function` | Runs the `code` of another `custom_function` node referenced by `config.custom_function_id` in the sandbox; its full `result` dict goes to `data[output_field]`. |

`_node_outputs[id]` gets `{output_field: output}` (template/mapping) or the raw result dict (custom_function mode).

### custom_function (builder.py:233-262, sandbox in backend/app/sandbox/runner.py)

Runs `config.code` in a RestrictedPython sandbox on a worker thread with `config.timeout_seconds` (default 30; a timeout returns an error, it does not kill the thread).

The code receives two variables:

- `state` — the full state dict (`state["data"]`, `state["output"]`, ...). When run as a **tool**, tool arguments are also at `state["arguments"]`.
- `result` — start with `{}` and write your outputs into it.

After execution:

- Only keys listed in `config.output_fields` are copied from `result` into `data` (`builder.py:248-251`). Undeclared keys remain visible only via `_node_outputs`.
- `output` = `str(result)` (Python repr of the whole result dict).
- `_node_outputs[id]` = full `result` dict.
- Any compile/runtime error or timeout fails the run (`RuntimeError`).

Available builtins are restricted to safe ones (`safe_builtins` + list/dict/set/min/max/sum/any/all/map/filter/enumerate/reversed — `sandbox/runner.py:14-27`). No imports, no filesystem, no network.

### human_in_loop

Raises `NotImplementedError` (Phase 3). The config schema exists (`input_fields`, `approval_required`, `timeout_seconds`) but the engine does nothing with it yet.

## 4. Edges and routing (builder.py:270-357)

Edge shape: `{id, source_node_id, source_handle, target_node_id, type: "static"|"conditional"|"error", condition?}`.

**Static graphs.** If a node has no conditional involvement, all its outgoing edges are added as plain LangGraph edges. (A node with multiple static out-edges fans out to all of them.)

**Conditional sources.** A node gets a router when it is a `conditional` node *or* has at least one outgoing edge with `type: "conditional"` and a `condition`:

- For **non-conditional nodes**, the conditions come from the edges themselves (`edge.condition`, evaluated in edge order), and the fallback is the `"default"` handle, else the first static edge.
- For **conditional nodes**, conditions come from `config.conditions` (see above).
- The router returns a `source_handle`; the path map translates each handle to its target node id (or `END`).

The `"error"` edge type exists in the schema but is not wired to any error-handling behavior yet — exceptions fail the whole run.

## 5. Condition expressions (backend/app/engine/conditions.py)

### json_path

Two forms:

1. **Comparison:** `<path> <op> <literal>` where op ∈ `== != >= <= > <`.
   - Paths: dot-separated, optional `$` / `$.` prefix; dict keys and list indices (`$.data.items.0.name`). Missing key → `None`.
   - Literals: quoted strings, `true`/`false`, `none`/`null`, integers, floats.
   - Ordering comparisons involving `None` are always false; comparing incompatible types (e.g. string vs number with `<`) raises `ConditionError` and fails the run.
2. **Truthiness:** a bare path (`$.data.flag`) is true when the resolved value is truthy; missing → false.

### regex

`re.search(pattern, state["output"])` — matches only against the current `output` string (the last node's output), not against `data`.

### llm

Not implemented yet (`NotImplementedError`).

## 6. Tools

Tools are defined at workflow level (`tools[]`) and attached to agents via `tool_ids`.

- **Schema:** each tool becomes an OpenAI function-calling schema — `name`, `description`, JSON-schema `parameters` built from `ToolDefinition.parameters` (`engine/tools.py:10-35`).
- **Execution** (`engine/tools.py:54-94`), per implementation type:
  - `custom_function`: runs `config.code` in the same sandbox as custom_function nodes, with tool arguments at `state["arguments"]`. Result is `json.dumps(result)`, or `{"error": ...}` on failure.
  - `builtin`: dispatches to a registered handler (`config.function`). Only `echo` exists so far.
  - `http`: issues the request to `config.url` (GET passes arguments as query params; other methods send them as JSON body). Returns `{"status", "body"}` or `{"error"}`.
- Tool results go back to the LLM as `tool` messages (JSON strings). An unknown tool name in a model response yields `{"error": "Unknown tool: ..."}` rather than failing the run.

## 7. Response shape

`POST /api/workflows/{id}/run` returns a `WorkflowRun` (`schema/models.py:341-354`, built in `backend/app/api/runs.py`):

```jsonc
{
  "id": "<run uuid>",
  "workflow_id": "...",
  "status": "completed | failed",     // runs are synchronous; no pending/paused yet
  "input_data": { ... },              // echo of the request body
  "output_data": {                    // present on success (runner.py:73-78)
    "output": "...",                  // final state["output"]
    "messages": [ ... ],              // full chat history
    "data": { ... },                  // final data bag
    "node_outputs": { "<node id>": { ... } }   // per-node results
  },
  "error": null,                      // set on failure (exception string)
  "events": [ ... ],                  // currently only a node_error event on failure
  "started_at": null,                 // not populated yet
  "completed_at": 1720000000.0,
  "total_tokens_input": 0,            // not aggregated yet (provider returns per-call counts)
  "total_tokens_output": 0,
  "estimated_cost_usd": 0.0
}
```

## 8. Worked example: `samples/sample-order-assistant.json`

Workflow: `start → assistant (agent, 2 tools) → format (transform template) → end`.
Run input: `{"request": "How much is order A-1002 with 8% tax?"}`

1. **Input mapping:** `request` is not reserved → `state = {messages: [], output: "", error: "", data: {"request": "How much..."}, _node_outputs: {}}`.
2. **start:** graph entry; follows its static edge to `assistant`.
3. **assistant (agent):**
   - No user/assistant message exists → injects user message `'{"request": "How much is order A-1002 with 8% tax?"}'` (the JSON-dumped `data`).
   - LLM calls `lookup_order(order_id="A-1002")` → sandbox code returns `{"found": true, "order": {"item": "Mouse", "qty": 2, "price": 25.5, "status": "processing"}}` → appended as a tool message.
   - LLM calls `calculate_total(subtotal=51.0, tax_rate=0.08)` → `{"subtotal": 51.0, "tax_amount": 4.08, "total": 55.08}` → appended as a tool message.
   - LLM produces the final answer with no tool calls → loop ends.
   - State now: `messages` has [system, user, assistant(tool_calls), tool, assistant(tool_calls), tool, assistant(final)]; `output` = final answer; `_node_outputs.assistant.content` = final answer.
4. **format (transform, template):** renders `"Support response:\n\n{{output}}"` → writes that string to `data.output` and `output`.
5. **end:** edge rewired to LangGraph `END`; graph completes.
6. **Response:** `status: "completed"`, `output_data.output` = the formatted answer, `output_data.node_outputs` contains both `assistant` and `format` entries, `output_data.data` contains the original `request` plus `output`.

## 9. Quick mental model

- **`data` is the bus.** Inputs go in, nodes add fields, conditions/templates/sandbox code read it.
- **`output` is a cursor.** Always "what the last node said"; fine for sequential chains, not for parallel branches.
- **`messages` is sticky.** Agents share one conversation per run.
- **`_node_outputs` is for inspection**, not routing — if a downstream node needs a value, write it to `data`.
