# Human-in-the-Loop, Auto Mode, and Persistent Checkpointing

This document describes how the Extra engine gates every tool call through a
centralized, **deterministic** approval layer, how it interrupts and resumes
runs, and how it selects a checkpointer. It applies uniformly to **all** tool
providers — local plugin tools and tools discovered dynamically from MCP servers
— because the mechanism lives in the engine's tool-execution path, not inside any
one provider.

> Read with [ARCHITECTURE.md](ARCHITECTURE.md), [YAML_SPEC.md](YAML_SPEC.md)
> (`auto`), and [MCP_AND_TOOLS.md](MCP_AND_TOOLS.md).

## 1. Overview

By default **every** tool call an agent requests requires explicit human
approval before the tool runs. Approval is **not** requested when either:

1. the tool was already approved **for this session** (a prior
   `ALLOW_FOR_SESSION` decision), or
2. the agent has `auto: true` in its YAML config.

There is **intentionally no LLM-based risk classification** in this version. No
tools are sent to a model at startup, no risk levels are computed, and no
per-tool configuration decides whether approval is needed. The rule is simple and
deterministic:

```text
if agent.auto:                 approval NOT required  (execute now)
elif tool allowed for session: approval NOT required  (execute now)
else:                          approval REQUIRED       (ask the human)
```

The human's decision is one of three typed values:

```text
ALLOW_ONCE          run this one invocation; ask again next time
ALLOW_FOR_SESSION   run it and stop asking for this tool this session
DENY                do not run it; store nothing
```

## 2. Tool-execution flow

```text
Agent / LangGraph node
        │  requests a concrete tool call
        ▼
AgentNode._invoke_tool           (the single choke point for local + MCP tools)
        │
        ▼
ApprovalCoordinator.resolve()    (centralized; no tool-specific logic)
        │   ├── ApprovalPolicy → requires approval?  (auto → session → ask)
        │   ├── if not required → execute
        │   └── if required → ApprovalProvider.request_decision()  (interrupt)
        ▼
   execute=True  ──► idempotency guard ─► provider (LocalTool / MCP tools/call)
   execute=False ──► structured "[denied]" tool result (no execution)
```

There is **no** path that reaches a provider without passing through
`ApprovalCoordinator.resolve()`. The engine uses a custom tool loop
(`run_tool_loop`), not a bypassing LangGraph `ToolNode`, and both local and MCP
tools are invoked from the same `_invoke_tool`.

Components (all small, single-responsibility):

| Component | Responsibility |
| --- | --- |
| `ApprovalDecision` / `parse_decision` | Typed decision + the single free-text parsing boundary |
| `ApprovalPolicy` / `DefaultApprovalPolicy` | Pure "does this need approval?" rule |
| `SessionApprovalStore` | Where `ALLOW_FOR_SESSION` permissions live (in-memory default) |
| `ApprovalProvider` | Requests a decision from a human (frontend-agnostic) |
| `ApprovalCoordinator` | Wires policy + session store + provider together |
| `ApprovalManager` | Pending-approval lifecycle + atomic resume claim |
| `ToolExecutionManager` | Execution idempotency ledger |
| `CheckpointProviderFactory` | Selects the checkpointer once at startup |

The policy, session store, and provider are all injected behind interfaces
(Dependency Inversion) — nothing is hardcoded into the coordinator, nodes, or
providers.

### Where a future risk-classification policy would plug in

`ApprovalPolicy` is the extension point. A smarter policy — for example one that
auto-executes provably read-only tools or escalates destructive arguments — can
be supplied to `ApprovalCoordinator` without changing the coordinator, the
session store, the provider, or the runtime tool-execution boundary. The current
`DefaultApprovalPolicy` deliberately performs no classification.

## 3. Decisions and their scope

The three decisions are parsed from user-facing text at exactly one boundary
(`parse_decision`), which accepts values such as `approve`, `allow`, `allow once`,
`allow for this session`, `deny`, and `reject`. Everywhere else the code uses the
typed `ApprovalDecision` enum — no raw-string comparisons.

- **`ALLOW_ONCE`** — the current invocation executes; nothing is persisted, so the
  next invocation of the same tool is asked again. It applies only to the exact
  pending invocation (identified by `tool_call_id`).
- **`ALLOW_FOR_SESSION`** — the current invocation executes and a session
  permission is stored, keyed by **tool identity** (not by argument values), so
  every later call of that tool in the same session runs without asking.
- **`DENY`** — the tool does not run, nothing is stored, and a structured
  `[denied]` result is returned to the model. A denial is a normal outcome, not a
  system failure: the agent can explain it, try another action, or finish.

### Session-permission key and lifecycle

A session permission is keyed by:

```text
session_id + agent_id + tool_identity
```

- `session_id` is the **conversation id** carried on `RunContext`
  (`conversation_id`, from the `X-Session-Id` request header). A conversation
  spans multiple runs, so `ALLOW_FOR_SESSION` persists across the runs of one
  conversation. It is **never** the `run_id`, agent id, process, thread, or user
  id alone. When there is no session id, no key exists — so a session permission
  can be neither found nor stored, and approval is required every time
  (fail-closed).
- `tool_identity` is a stable `provider:namespace:tool` string (MCP tools are
  namespaced by their server id). Two MCP servers exposing the same short tool
  name therefore never share a permission.

The default `SessionApprovalStore` keeps permissions in a process-local, async-safe
set. **They are lost when the process restarts** — this is intentional and
isolated behind the store interface. A distributed deployment can supply another
implementation of the same protocol (e.g. Redis keyed by the same tuple) without
touching callers.

## 4. `auto`

`auto` is an optional agent-level flag (default `false`; YAML key `auto`, with
`auto_mode` accepted as an alias — see [YAML_SPEC.md](YAML_SPEC.md)).

```text
auto: false / missing   →  every tool call requires approval (unless session-allowed)
auto: true              →  every tool call executes immediately; provider never invoked
```

`auto` is evaluated **per agent** inside the coordinator, so enabling it for one
agent never enables automatic execution for another agent in the same run. In
auto mode no approval is requested and no session permission is stored.

## 5. Interrupt and resume

When a call requires approval, the `ApprovalProvider` for this runtime
(`InterruptApprovalProvider`):

1. Persists a sanitized `ApprovalRecord` (masked arguments only) and moves the run
   to `PENDING_APPROVAL`.
2. Calls LangGraph `interrupt(payload)`, which persists a **checkpoint** under the
   run's `thread_id` and returns control to the caller.
3. `Engine.run()` detects the interrupt and returns a `RunResult` with
   `status="pending_approval"` and a sanitized `pending_approval` payload. **No
   tool ran; for MCP, no `tools/call` was sent.**

To resume, `Engine.resume(run_id, approval_id, decision)`:

1. **Atomically claims** the approval (`PENDING → RESUMING`) — exactly one caller
   wins (see §7).
2. Resumes the **same** LangGraph thread from its checkpoint via
   `Command(resume={"decision": ...})`. The graph is not restarted, the agent is
   not re-selected, and intent/planning are not re-run.
3. The node re-executes, the coordinator interprets the typed decision, and:
   - `ALLOW_ONCE` / `ALLOW_FOR_SESSION` → the original tool call executes and its
     result flows back to the agent (and `ALLOW_FOR_SESSION` records a session
     permission);
   - `DENY` → a structured `[denied]` result is returned and **no** tool/MCP call
     is made.

An unrecognized or missing resume decision **fails closed to `DENY`**, so an
ambiguous resume never triggers a side effect.

Because a LangGraph node re-executes from its start on resume, tool *preparation*
is deterministic and safe to repeat, and all non-idempotent execution happens
**after** the interrupt returns. Duplicate execution on re-entry is prevented by
the idempotency ledger (§7).

Identifiers are kept strictly separate:

```text
run_id        business-level run id
thread_id     LangGraph checkpoint id (equals run_id here)
approval_id   one pending approval
tool_call_id  the agent's requested tool call
execution_id  one actual execution attempt (idempotency key)
```

Run states: `RUNNING → PENDING_APPROVAL → RESUMING → COMPLETED | FAILED`, with
validated transitions (no `COMPLETED → RESUMING`, no `REJECTED → APPROVED`).

## 6. Checkpointer selection

`CheckpointProviderFactory` chooses the checkpointer **once** at startup; nothing
else in the engine branches on the backend:

```text
checkpoint connection string provided  → persistent, shared checkpointer (PostgreSQL)
no connection string                   → in-memory checkpointer (+ startup warning)
```

After construction, interrupt / checkpoint / resume are identical for both. The
graph is always compiled with a checkpointer, so the HITL path is the same in
local development and production.

### In-memory limitations

The in-memory checkpointer is process-local. On selection the engine logs a
warning that it: is not shared between replicas/Pods, is lost on restart, and
cannot resume a run on another instance. It is intended for local development and
tests, never for multi-replica production. The default session-approval store has
the same lifetime.

### Cross-Pod resume

With a shared persistent checkpointer, the run state belongs to the `thread_id`,
not the Pod. Pod A can start a run and reach an interrupt; the approval can later
be delivered to Pod C, which loads the same `thread_id` and resumes. No sticky
sessions are required. (Cross-Pod `ALLOW_FOR_SESSION` additionally requires a
shared `SessionApprovalStore` implementation.)

## 7. Concurrency and idempotency

**Each pending approval is uniquely identified** by its `tool_call_id`, so a
decision can never be matched to a different pending invocation. Concurrent
invocations are resolved independently; a decision for one never approves another.

**Resume deduplication.** Before resuming, `ApprovalManager.claim()` performs an
atomic `PENDING → RESUMING` compare-and-set. Only one concurrent request wins;
others get a stable `ApprovalAlreadyProcessed`. In-memory this is a locked
transition; the shared-DB implementation maps it to a conditional
`UPDATE ... WHERE status = 'pending'`.

**Session-store safety.** The in-memory session store guards its state with a
lock, so concurrent `ALLOW_FOR_SESSION` grants and lookups cannot corrupt it.

**Execution idempotency.** Each execution attempt has a stable `execution_id`
derived from the `tool_call_id`. Before running a tool the engine checks the
`ToolExecutionRepository`; a completed prior attempt short-circuits and returns
the recorded result instead of causing a second side effect. This is the primary
protection against duplicate external effects (email, payments, deletes) on
retry or graph re-entry.

Delivery semantics are **at-least-once minus detected duplicates**. If an
external system offers idempotency keys, propagate the `execution_id`; if not,
the ledger minimizes and detects duplicates but the engine does not claim
exactly-once for systems that cannot support it.

## 8. API contracts

The stateless engine API (`agent_engine/api/app.py`) exposes:

```text
POST /invoke                                         → run; may return status=pending_approval
GET  /runs/{run_id}                                  → run status + any pending approval
POST /runs/{run_id}/approvals/{approval_id}/decision → resume with a free-text decision
POST /runs/{run_id}/approvals/{approval_id}/approve  → resume with ALLOW_ONCE
POST /runs/{run_id}/approvals/{approval_id}/reject   → resume with DENY
```

The `/decision` endpoint accepts a free-text `decision` (`allow`,
`allow for this session`, `deny`, …), parsed to a typed `ApprovalDecision` at that
single boundary; an unrecognized value returns 400. `/approve` and `/reject` are
convenience routes for the two most common typed decisions. To grant
`ALLOW_FOR_SESSION`, use `/decision` with `"allow for this session"`.

A pending-approval payload contains `run_id`, `approval_id`, `agent_id`,
`tool_name`, a human-readable `description` (stating the tool has **not** run
yet), `provider`, and **masked** `arguments`. Approval endpoints validate that the
run and approval exist, the approval belongs to the run, the caller is authorized,
the approval is still pending, and the run is resumable. Errors map to stable
status codes (404 / 403 / 409 / 400) without leaking internals. Secrets, tokens,
and unmasked arguments are never persisted or returned.

## 9. Security notes

Approval records and the approval request store only masked, non-secret data and
an `auth_ref` reference — never access tokens, MCP session tokens, or raw
authorization headers. The sanitizer masks common credential-bearing keys
(`password`, `token`, `secret`, `authorization`, `api_key`, `access_token`,
`refresh_token`, `cookie`, …) recursively through nested structures, and returns a
copy so the arguments handed to the tool are never mutated. Valid credentials are
resolved again at resume time. Structured logs use identifiers and safe metadata
(`run_id`, `approval_id`, `tool_call_id`, `execution_id`, `decision`, …), never
argument bodies.

## 10. Known limitations / decisions

- **No risk classification (by design).** This version asks for approval on every
  tool by default. A future `ApprovalPolicy` implementation can add risk-based
  auto-execution at the extension point in §2 without reworking the workflow.
- **In-memory session approvals.** The default `SessionApprovalStore` is
  process-local and lost on restart. This is explicit and isolated behind the
  store interface; a distributed implementation can replace it.
- **Single-node re-execution.** With the current one-node-per-graph-node design,
  resuming replays the node from its start (LangGraph semantics). Preparation is
  deterministic and the idempotency ledger prevents duplicate side effects; a
  future optimization is to split the agent turn into separate LLM/tools nodes so
  only the tools node replays.
- **Shared-DB repositories.** The in-memory `Run`/`Approval`/`ToolExecution`
  repositories back local development. Multi-Pod resume requires a shared
  persistent checkpointer (PostgreSQL), shared DB-backed repositories, and a
  shared session store that implement the same contracts.
