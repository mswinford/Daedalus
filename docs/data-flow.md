# Data Flow Reference

How a workflow run flows from HTTP request to response, and what each node type reads and writes. All references are to the current engine code (function names + file; line numbers drift as the code grows).

---

## 1. The big picture

```
POST /api/workflows/{id}/run  {"request": "...", "score": 95, ...}
        │
        ▼
_load_workflow()          loads ~/.ai-forge/workflows/{id}.json, validates against Pydantic schema
        │
        ▼
RunRecord created         in-memory run store (status=running), run_id = uuid4
        │  returns HTTP 202 + {run_id} immediately; the graph runs in a worker thread
        ▼
runner.run_workflow_sync  backend/app/engine/runner.py
        │  1. validate input against state_schema (if defined)
        │  2. build LangGraph graph from workflow JSON (GraphBuilder), with MemorySaver checkpointer
        │  3. map run input → initial state
        │  4. execute graph; each node emits a RunEvent → broadcast to WS subscribers
        │     (a human_in_loop node pauses the graph via interrupt())
        ▼
GET /api/runs/{id}        poll for status + result   ·   WS /api/runs/{id}/events stream live
POST /api/runs/{id}/resume  resume a paused run with Command(resume=human_input)
```

Runs are **asynchronous**: `POST .../run` returns `202` + `run_id` and the graph executes in a worker thread (`asyncio.to_thread`). Events stream over WebSocket; the finished `WorkflowRun` is retrievable by polling. A human-in-loop node pauses the run (LangGraph `interrupt()`); it is resumed later via `POST /api/runs/{id}/resume`, which calls `runner.resume_workflow` with a `Command(resume=...)` against the SQLite checkpointer (`~/.ai-forge/checkpoints.db`, one connection per run). A run can be cancelled at any non-terminal point via `POST /api/runs/{id}/cancel`: a paused run is terminated immediately and its checkpoint thread is deleted (so restart-recovery can never resurrect it); a running run stops at the next super-step boundary — the runner drives the graph with `astream(stream_mode="values")` and checks a per-run cancel flag between steps, so the in-flight node's work finishes first.

The workflow JSON is translated into a LangGraph `StateGraph` once per run (`builder.py`). Every node is an async function that receives the shared state and returns the parts of the state it changed. Each node is wrapped by `_instrument`, which emits `node_start` / `node_end` (with duration + summarized output) events and re-raises LangGraph's `GraphInterrupt` untouched. Any other exception becomes a fatal `node_error` event — **unless** the node owns an error edge (`error_handling` opt-in), in which case the exception is converted into the `_error_info` state marker instead so the router can take the error path (see §4).

## 2. The shared state

Every node reads and writes one state object with six channels (built in `runner._build_initial_state`):

| Channel | Type | Purpose |
|---|---|---|
| `messages_by_node` | dict | Chat history **per agent node**, keyed by node id. Each agent appends only to its own slice (`state["messages_by_node"][node_id]`); a different agent does **not** see it. |
| `output` | str | The most recent node's primary output string. Overwritten by each node. This is what regex conditions match against and what `{{output}}` templates render. |
| `error` | str | Reserved. Currently no node writes it; failures use the `_error_info` marker below or fail the run. |
| `_error_info` | dict | Transient failure marker: `{node_id, error}` while a node's exception is being routed to its error edge; cleared (set to `{}`) by the next successful node. Not addressable by conditions. |
| `data` | dict | The general-purpose data bag. Run inputs land here; nodes add fields here for downstream use. Addressable as `$.data.<field>` in conditions/templates. **This is the only thing agents share with each other.** |
| `_node_outputs` | dict | Per-node results, keyed by node id. In the HTTP response this is returned as `node_outputs`. Not addressable by conditions (use `data`), but useful for debugging and templates (`{{_node_outputs.grade.label}}`). |

### Input mapping (`runner._build_initial_state`)

Run input fields are split into two groups:

- **Reserved keys** (`messages_by_node`, `output`, `error`, `data`, `_node_outputs`) map directly onto the state channel of the same name — i.e. they *override* the default.
- **Everything else** is collected under `data`: input `{"score": 95, "text": "hi"}` → `state["data"] = {"score": 95, "text": "hi"}`.

So any field you pass in a run body is available as `$.data.<field>` to conditions, templates, sandbox code, and agents.

## 3. Node types: what each one reads and writes

| Node | Reads | Writes | Routing after it |
|---|---|---|---|
| `start` | — (not a real function) | — | Static edge(s); entry point of the graph |
| `end` | — | — | Terminal (LangGraph `END`) |
| `agent` | `messages_by_node[node_id]`, `data` (if no conversation yet), model config, tools | `messages_by_node[node_id]`, `output`, `_node_outputs[id]` | Static or conditional edges |
| `conditional` | — (passthrough function) | nothing | **Router**: conditions decide which branch edge |
| `transform` | state via template paths / field mappings / referenced sandbox code | `output`, `data[output_field]`, `_node_outputs[id]` | Static or conditional edges |
| `custom_function` | full `state` (sandbox variable) | `output`, `data[<declared output_fields>]`, `_node_outputs[id]` | Static or conditional edges |
| `human_in_loop` | `input_fields` from state; resumes with the human's response | `output`, `data[<output_fields>]`, `_node_outputs[id]` | Static or conditional edges (or fails on reject) |
| `invoke` | capability resolved from the registry by `name@version` (pinned per run); entry gate reads mapped inputs, inner nodes read/write the call frame's channels | `_node_outputs[invoke_id]`, `data[output_field]` (sub result) | Static or conditional edges; region failures route to its error edge if present |

### start / end

- `start` is not added to the graph as a node; its outgoing edges are wired from LangGraph's `START`. If the workflow has no start node, the first non-end node becomes the entry point.
- Edges pointing at an `end` node are rewired to LangGraph's `END`. The end node's `output_fields` config is declarative only — it does not filter the response.

### agent (`builder.py`, the agent node builder)

1. Resolves its LLM provider from `workflow.models` via `config.model_id` (fails with `ValueError` if missing).
2. Builds the prompt: `[system message from config.system_prompt] + state["messages_by_node"][node.id]`.
3. **Input injection:** if there is no `user` or `assistant` message in *that node's* list yet, it appends one user message containing the **entire `data` dict as JSON**. Empty `data` → the literal string `"Begin."`. There is no per-field selection — the LLM sees all of `data`.
4. **Tool loop** (up to `config.max_iterations`, default 10): calls the provider; if the response has no `tool_calls`, it's final and the loop breaks. Otherwise each tool call is executed (see [Tools](#6-tools)) and the results are appended as `tool` messages before the next LLM call.
5. Writes back:
    - `messages_by_node[node.id]` = previous slice + final assistant message
    - `output` = final content string
    - `_node_outputs[node.id]` = `{"content": final_content}`

**Per-agent isolation:** because each agent reads and writes only its own `messages_by_node[node_id]` slice, two agent nodes in the same run keep **independent conversations**. Agent B does *not* see agent A's messages — but it *does* see everything agent A wrote to `data`, so structured hand-offs work while chat context stays separate.

### conditional (`builder.py`)

The node function itself is a **passthrough** (`return state`). All logic lives in the router built for its outgoing edges (`_make_router`):

- `config.conditions[i]` is evaluated against the current state, in order.
- The first condition that matches routes to the **i-th non-`default` outgoing edge** of this node (in workflow edge order).
- If none match, it takes `config.default_branch`'s handle if set, else the `"default"` handle.
- If nothing matches and there is no default, the run fails with `ConditionError`.

### transform (`builder.py`)

Three modes, selected by `config.mode`; all write to `data[config.output_field]` and `output`:

| Mode | Behavior |
|---|---|
| `template` | Renders `config.template`, replacing `{{path}}` placeholders. Paths are dot-separated, resolved against the **whole state** (`{{output}}`, `{{data.score}}`, `{{_node_outputs.grade.label}}`). Missing paths render as empty string. |
| `mapping` | For each `field_mappings` entry, resolves `source` (same path syntax) and puts it under `target`; missing → `""`. The resulting dict is stringified (`str(...)`) into `output` — note this is Python repr, not JSON. |
| `custom_function` | Runs the `code` of another `custom_function` node referenced by `config.custom_function_id` in the sandbox; its full `result` dict goes to `data[output_field]`. |

`_node_outputs[id]` gets `{output_field: output}` (template/mapping) or the raw result dict (custom_function mode).

### custom_function (`builder.py`, sandbox in `backend/app/sandbox/runner.py`)

Runs `config.code` in a RestrictedPython sandbox on a worker thread with `config.timeout_seconds` (default 30; a timeout returns an error, it does not kill the thread).

The code receives two variables:

- `state` — the full state dict (`state["data"]`, `state["output"]`, ...). When run as a **tool**, tool arguments are also at `state["arguments"]`.
- `result` — start with `{}` and write your outputs into it.

After execution:

- Only keys listed in `config.output_fields` are copied from `result` into `data`. Undeclared keys remain visible only via `_node_outputs`.
- `output` = `str(result)` (Python repr of the whole result dict).
- `_node_outputs[id]` = full `result` dict.
- Any compile/runtime error or timeout raises `RuntimeError` — routed to the node's error edge if it has one, otherwise the run fails.

Available builtins are restricted to safe ones (`safe_builtins` + list/dict/set/min/max/sum/any/all/map/filter/enumerate/reversed). No imports, no filesystem, no network. Custom functions can also call `get_secret(name)` to read a value from the secrets store.

### human_in_loop (`builder.py`)

Pauses the run using LangGraph's `interrupt()`, emitting a `human_request` event with a structured payload (node id, message, declared `input_fields`, and `approval_required`). The graph is checkpointed to SQLite (`~/.ai-forge/checkpoints.db`, one connection per run); the run's status becomes `paused`.

On resume (`POST /api/runs/{id}/resume`), the human's response arrives via `Command(resume=...)`:

- If `config.approval_required` is set and the payload is `{approved: false}`, the node raises a `RuntimeError` — the run fails (or routes to an error edge if one exists).
- Otherwise the response dict is mapped onto `data` using `output_fields` (1-to-1 by name, positional when there are several, or merged whole when no `output_fields` are declared), and written to `output` + `_node_outputs[id]`.

`timeout_seconds` (default `None` = indefinite) arms an asyncio timer at pause; on expiry the run fails with a terminal `human_timeout` event unless resumed first — resume cancels the timer. The interrupt payload carries `timeout_seconds` + `requested_at` so the frontend shows a live countdown.

A paused run can also be abandoned: `POST /api/runs/{id}/cancel` terminates it with a `run_cancelled` event, disarms the timeout, and deletes its checkpoint thread — so indefinite waits can no longer accumulate as zombie approvals across restarts.

### invoke (`expand.py`, `nodes/invoke.py`, `nodes/invoke_exit.py`)

References a registry capability by `name@version`; only `tool` and `workflow` kinds are invokable. The version resolves at run start and is **pinned to the run**, so pause/resume/restart re-expands the identical structure. A deleted pinned version fails loudly — resume returns 422, startup recovery marks the run failed with a terminal fatal event — it never silently re-resolves to a newer version.

- **Tool kind:** fetches the `ToolDefinition` and executes it directly — no frame.
- **Workflow kind:** at graph BUILD time, `expand()` splices the sub-workflow into the parent graph — inner node ids are prefixed `{invoke_id}__`, edges are re-routed through the invoke node (the builder maps `start`/`end` to LangGraph sentinels), and the sub's `models[]` is merged with a suffix-on-clash. A synthetic `invoke_exit` gate closes the region.
- **Call frame:** the entry gate validates mapped inputs against the sub's `state_schema`, swaps ALL state channels to the restricted frame (`data` = mapped inputs only, `output` reset), and stashes parent state under `_invoke_stash[invoke_id]`. Inner nodes run exactly as authored — no reference rewriting. The exit gate restores parent state and writes the result to `_node_outputs[invoke_id]` + `data[output_field]`.
- **Errors:** when the invoke node has a parent `type="error"` edge, expansion keeps a copy on the entry gate (its own input-validation failures get caught) and adds synthetic error edges from every inner node lacking its own to the exit gate; the exit gate re-keys `_error_info` to its own id so the router can take the parent's error edge, and skips output writes (no result was produced). Without a parent error edge, an inner failure fails the run at the prefixed node id.

## 4. Edges and routing (`builder._build_edges`)

Edge shape: `{id, source_node_id, source_handle, target_node_id, type: "static"|"conditional"|"error", condition?}`.

**Static graphs.** If a node has no conditional involvement, all its outgoing edges are added as plain LangGraph edges. (A node with multiple static out-edges fans out to all of them.)

**Conditional sources.** A node gets a router when it is a `conditional` node *or* has at least one outgoing edge with `type: "conditional"` and a `condition`:

- For **non-conditional nodes**, the conditions come from the edges themselves (`edge.condition`, evaluated in edge order), and the fallback is the `"default"` handle, else the first static edge.
- For **conditional nodes**, conditions come from `config.conditions` (see above).
- The router returns a `source_handle`; the path map translates each handle to its target node id (or `END`).

**Error edges.** A node that opted in to error handling (`Node.error_handling`) may own one outgoing edge with `type == "error"` (source handle `"error"`, red dashed in the UI). When that node raises, `_instrument` stores the exception in `_error_info` instead of failing the run, and the router checks the marker **before** normal routing: if it is set, the run follows the error edge; otherwise routing proceeds as usual and a successful node clears the marker. A HIL pause (`GraphInterrupt`) is never treated as a failure — it always re-raises and pauses the run. Error edges are excluded from conditional branch matching, so a conditional node can carry both condition branches and an error handle. Validation: at most one error edge per source, none from `start`, and the source must also have a non-error fallback path.

## 5. Condition expressions (`backend/app/engine/conditions.py`)

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

- **Schema:** each tool becomes an OpenAI function-calling schema — `name`, `description`, JSON-schema `parameters` built from `ToolDefinition.parameters` (`engine/tools.py`).
- **Execution** (`engine.execute_tool`), per implementation type:
  - `custom_function`: runs `config.code` in the same sandbox as custom_function nodes, with tool arguments at `state["arguments"]`. Result is `json.dumps(result)`, or `{"error": ...}` on failure.
  - `builtin`: dispatches to a registered handler (`config.function`). Only `echo` exists so far.
  - `http`: issues the request to `config.url` (GET passes arguments as query params; other methods send them as JSON body). The URL and headers support `{placeholder}` templating from state, and header values may reference secrets via `${NAME}`. Honors `config.timeout_seconds`. Returns `{"status", "body"}` or `{"error"}`.
- Tool results go back to the LLM as `tool` messages (JSON strings). An unknown tool name in a model response yields `{"error": "Unknown tool: ..."}` rather than failing the run.

## 7. Response shape

`POST /api/workflows/{id}/run` returns `202` + `{run_id}` immediately. The finished run is fetched from `GET /api/runs/{id}`, which returns a `WorkflowRun` (`schema/models.py`, built in `backend/app/api/runs.py`):

```jsonc
{
  "id": "<run uuid>",
  "workflow_id": "...",
  "status": "pending | running | completed | failed | paused",   // async; paused = waiting on human_in_loop
  "input_data": { ... },              // echo of the request body
  "output_data": {                    // present on success
    "output": "...",                  // final state["output"]
    "messages_by_node": { "<node id>": [ ... ] },   // per-agent chat history
    "data": { ... },                  // final data bag
    "node_outputs": { "<node id>": { ... } }        // per-node results
  },
  "error": null,                      // set on failure (exception string)
  "interrupt_value": null,            // set while paused: the human_request payload to act on
  "started_at": 1720000000.0,
  "completed_at": 1720000000.0,       // null until finished
  "total_tokens_input": 1234,         // aggregated across all LLM calls in the run
  "total_tokens_output": 567,
  "estimated_cost_usd": 0.0123        // from model pricing (per-token or per-1k)
}
```

The live event stream (`WS /api/runs/{id}/events`) replays past events on connect, then streams new ones: `node_start`, `node_end` (duration + summarized output), `llm_call` (per-call token counts), `human_request`, and a terminal `run_end` / fatal `node_error`.

## 8. Worked example: `backend/app/templates/sample-order-assistant.json`

Workflow: `start → assistant (agent, 2 tools) → format (transform template) → end`.
Run input: `{"request": "How much is order A-1002 with 8% tax?"}`

1. **Input mapping:** `request` is not reserved → `state = {messages_by_node: {}, output: "", error: "", data: {"request": "How much..."}, _node_outputs: {}}`.
2. **start:** graph entry; follows its static edge to `assistant`.
3. **assistant (agent):**
    - No user/assistant message exists in `messages_by_node.assistant` → injects user message `'{"request": "How much is order A-1002 with 8% tax?"}'` (the JSON-dumped `data`).
    - LLM calls `lookup_order(order_id="A-1002")` → sandbox code returns `{"found": true, "order": {"item": "Mouse", "qty": 2, "price": 25.5, "status": "processing"}}` → appended as a tool message.
    - LLM calls `calculate_total(subtotal=51.0, tax_rate=0.08)` → `{"subtotal": 51.0, "tax_amount": 4.08, "total": 55.08}` → appended as a tool message.
    - LLM produces the final answer with no tool calls → loop ends.
    - State now: `messages_by_node.assistant` has [system, user, assistant(tool_calls), tool, assistant(tool_calls), tool, assistant(final)]; `output` = final answer; `_node_outputs.assistant.content` = final answer.
4. **format (transform, template):** renders `"Support response:\n\n{{output}}"` → writes that string to `data.output` and `output`.
5. **end:** edge rewired to LangGraph `END`; graph completes.
6. **Response:** `status: "completed"`, `output_data.output` = the formatted answer, `output_data.node_outputs` contains both `assistant` and `format` entries, `output_data.data` contains the original `request` plus `output`.

## 9. Quick mental model

- **`data` is the bus.** Inputs go in, nodes add fields, conditions/templates/sandbox code read it. It's also the only thing agents share with each other.
- **`output` is a cursor.** Always "what the last node said"; fine for sequential chains, not for parallel branches.
- **`messages_by_node` is per-agent.** Each agent keeps its own conversation; a second agent starts fresh but still sees everything in `data`.
- **`_node_outputs` is for inspection**, not routing — if a downstream node needs a value, write it to `data`.
