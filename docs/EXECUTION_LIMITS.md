# Execution limits

Execution limits are **runtime-enforced** guardrails applied to every run. They
exist to stop runaway executions before they cost money or hang: infinite
model→tool loops, an agent hammering the same tool, an orchestrator calling its
children over and over, and accidental duplicate calls.

They are enforced by the engine itself — **not** by prompts. A prompt can ask a
model to behave; only the runtime can guarantee it.

## What is limited

| Limit | Meaning | Default |
|---|---|---|
| `max_iterations` | model→tools→model rounds allowed for a single node | `20` |
| `max_tool_calls` | total local/MCP tool calls per run | `10` |
| `max_tool_calls_per_agent` | tool calls per agent per run | `4` |
| `max_child_agent_calls` | orchestrator→child-agent calls per run | `8` |
| `allow_duplicate_tool_calls` | if `false`, block identical repeats | `false` |

A **duplicate** is a call with the same `agent_id`, the same `tool_name`, and the
same serialized arguments, within one run.

## Why they exist

- **Prevent runaway loops.** A model that keeps requesting tools (or an
  orchestrator that keeps delegating) is capped instead of looping forever.
- **Bound cost.** Each tool call and model round is work; the caps make the
  worst case predictable.
- **Avoid accidental repeats.** Re-issuing an identical call wastes a round and
  often signals a stuck model; duplicates are blocked by default.

## Defaults

If the YAML has no `execution:` block, the conservative defaults above apply
automatically. Every limit is per **run** (one `engine.run` / `engine.stream`),
reset at the start of each run; `max_iterations` is tracked per node.

## How to override

Add a top-level `execution:` block to your `agents.yaml`. Any key you omit keeps
its default.

```yaml
execution:
  max_iterations: 20
  max_tool_calls: 10
  max_tool_calls_per_agent: 4
  max_child_agent_calls: 8
  allow_duplicate_tool_calls: false
```

Validation (offline, at parse time):
- the four integer limits must be **positive integers** (booleans and floats are
  rejected);
- `allow_duplicate_tool_calls` must be a **boolean**.

Invalid values fail `agentctl validate` / config parsing with a clear error.

## What happens when a limit is reached

Limits degrade **gracefully** — a run never crashes with an obscure exception:

- **Tool / child-agent limits and duplicates:** the call is **not executed**.
  The model receives a short controlled message (e.g. *"Tool call blocked: the
  'max_tool_calls' execution limit (10) was reached… finish with the information
  you have."*) and naturally wraps up.
- **`max_iterations`:** the node's tool loop **stops** and returns the latest
  model response. No further tools are called.

When a limit is hit the engine logs **safe metadata only** at `WARNING`:
`run_id`, `node_id`/`agent_id` (when available), the limit name, the current
count, and the configured limit. It never logs raw prompts, tool arguments, tool
results, headers, or secrets.

## Where it is enforced

- Policy parsed in `agent_engine/parsers/yaml/parser.py` → `ExecutionPolicy`
  (`agent_engine/core/execution.py`), carried on `SystemSpec.execution`.
- A per-run `ExecutionLimiter` (`agent_engine/runtime/execution.py`) is created
  in `LangGraphEngine.run`/`stream` and published on the `current_execution`
  context var.
- Enforced at three seams: the tool loop (`run_tool_loop` → iterations),
  `AgentNode._invoke_tool` (local/MCP tool calls + duplicates), and the
  orchestrator's child invocation (`max_child_agent_calls`).
