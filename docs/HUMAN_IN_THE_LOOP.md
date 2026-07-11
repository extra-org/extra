# Human-in-the-Loop, Auto Mode, and Persistent Checkpointing

This document describes how the Extra engine gates every tool call through a
centralized approval layer, how it interrupts and resumes runs, and how it
selects a checkpointer. It applies uniformly to **all** tool providers — local
plugin tools and tools discovered dynamically from MCP servers — because the
mechanism lives in the engine's tool-execution path, not inside any one
provider.

> Read with [ARCHITECTURE.md](ARCHITECTURE.md), [YAML_SPEC.md](YAML_SPEC.md)
> (`auto_mode`), and [MCP_AND_TOOLS.md](MCP_AND_TOOLS.md).

## 1. Overview

Every tool call an agent requests passes through a single centralized layer
before the tool runs. That layer produces one of three decisions:

```text
EXECUTE           run the tool now
REQUIRE_APPROVAL  pause the run, persist a checkpoint, wait for a human
DENY              never run the tool (auto_mode cannot override this)
```

The decision is made when the agent requests a **concrete** tool call, so the
**runtime arguments** — not just the static tool — determine the risk
(`manage_account(action="delete")` is destructive even though the name is
neutral).

## 2. Tool-execution flow

```text
Agent / LangGraph node
        │  requests a concrete tool call
        ▼
AgentNode._invoke_tool           (the single choke point for local + MCP tools)
        │
        ▼
ToolExecutionManager.decide()    (centralized)
        │   ├── ToolApprovalPolicy → RiskAssessment (EXECUTE / REQUIRE_APPROVAL / DENY)
        │   └── auto_mode applied  (REQUIRE_APPROVAL → EXECUTE; DENY unchanged)
        ▼
   EXECUTE ─────────────► idempotency guard ─► provider (LocalTool / MCP tools/call)
   DENY ───────────────► synthetic "[denied]" tool result (no execution)
   REQUIRE_APPROVAL ───► ApprovalManager.create_pending() → interrupt() → checkpoint
```

There is **no** path that reaches a provider without passing through
`ToolExecutionManager.decide()`. The engine uses a custom tool loop
(`run_tool_loop`), not a bypassing LangGraph `ToolNode`, and both local and MCP
tools are invoked from the same `_invoke_tool`.

Components (all small, single-responsibility):

| Component | Responsibility |
| --- | --- |
| `ToolApprovalPolicy` / `DefaultToolApprovalPolicy` | Pure risk verdict for a call |
| `ToolRiskClassifier` | Maps name + description + schema + args → `RiskCategory` |
| `ToolExecutionManager` | Applies policy + `auto_mode` + idempotency |
| `ApprovalManager` | Pending-approval lifecycle + atomic resume claim |
| `RunRepository` / `ApprovalRepository` / `ToolExecutionRepository` | Persistence contracts |
| `CheckpointProviderFactory` | Selects the checkpointer once at startup |

The policy is isolated behind an interface (`ToolApprovalPolicy`) and injected;
it is not hardcoded into the manager, nodes, or providers.

## 3. Approval policy

The engine derives risk **independently** and does not trust MCP-supplied hints
(`readOnlyHint`, `destructiveHint`, `approvalRequired`, `riskLevel`). Default
mapping (conservative, deterministic):

```text
read / search / list / fetch / query / inspect / calculate → EXECUTE
draft with no external side effect                          → EXECUTE
send or publish externally                                  → REQUIRE_APPROVAL
create or modify persistent data                            → REQUIRE_APPROVAL
delete / remove / revoke / disable / cancel                 → REQUIRE_APPROVAL
financial action                                            → REQUIRE_APPROVAL
permission / access-control change                          → REQUIRE_APPROVAL
code / command execution / deployment                       → REQUIRE_APPROVAL (DENY for high-severity)
unknown / ambiguous                                         → REQUIRE_APPROVAL (fail-safe)
forbidden (e.g. drop_database, factory_reset)               → DENY
```

Signals considered: tool name, description, input schema, **runtime arguments**,
provider type, and MCP server identity. A benign name with a risky argument
(`action: delete`) is escalated; a risky argument is never allowed to slip
through a read-like name as `EXECUTE`.

## 4. `auto_mode`

`auto_mode` is an optional agent-level flag (default `false`; see
[YAML_SPEC.md](YAML_SPEC.md)).

```text
auto_mode: false / missing   →  EXECUTE / REQUIRE_APPROVAL / DENY (approval interrupts active)
auto_mode: true              →  REQUIRE_APPROVAL is downgraded to EXECUTE; DENY still blocks
```

`auto_mode` is applied by `ToolExecutionManager`, **after** the policy runs, so
the policy itself stays a pure function of the call.

## 5. Interrupt and resume

When a call requires approval:

1. `ApprovalManager.create_pending()` persists an `ApprovalRecord` (sanitized
   arguments only) and moves the run to `PENDING_APPROVAL`.
2. The node calls LangGraph `interrupt(payload)`, which persists a **checkpoint**
   under the run's `thread_id` and returns control to the caller.
3. `Engine.run()` detects the interrupt and returns a `RunResult` with
   `status="pending_approval"` and a sanitized `pending_approval` payload. **No
   tool ran; for MCP, no `tools/call` was sent.**

To resume, `Engine.resume(run_id, approval_id, decision)`:

1. **Atomically claims** the approval (`PENDING → RESUMING`) — exactly one caller
   wins (see §7).
2. Resumes the **same** LangGraph thread from its checkpoint via
   `Command(resume=...)`. The graph is not restarted, the agent is not
   re-selected, and intent/planning are not re-run.
3. On `APPROVE`, the original tool call executes and its result flows back to the
   agent. On `REJECT`, a synthetic result (`status=rejected`, original
   `toolCallId`, reason) is returned instead and **no** tool/MCP call is made.

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
tests, never for multi-replica production.

### Cross-Pod resume

With a shared persistent checkpointer, the run state belongs to the `thread_id`,
not the Pod. Pod A can start a run and reach an interrupt; the approval can later
be delivered to Pod C, which loads the same `thread_id` and resumes. No sticky
sessions are required.

## 7. Concurrency and idempotency

**Resume deduplication.** Before resuming, `ApprovalManager.claim()` performs an
atomic `PENDING → RESUMING` compare-and-set. Only one concurrent request wins;
others get a stable `ApprovalAlreadyProcessed`. In-memory this is a locked
transition; the shared-DB implementation maps it to a conditional
`UPDATE ... WHERE status = 'pending'`.

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
POST /invoke                                   → run; may return status=pending_approval + pending_approval
GET  /runs/{run_id}                            → run status + any pending approval
POST /runs/{run_id}/approvals/{approval_id}/approve  → resume with APPROVE
POST /runs/{run_id}/approvals/{approval_id}/reject   → resume with REJECT
```

A pending-approval payload contains `run_id`, `approval_id`, `agent_id`,
`tool_name`, `reason`, `category`, `provider`, and **sanitized** `arguments`.
Approval endpoints validate that the run and approval exist, the approval belongs
to the run, the caller is authorized, the approval is still pending, and the run
is resumable. Errors map to stable status codes (404 / 403 / 409 / 400) without
leaking internals. Secrets, tokens, and unredacted arguments are never persisted
or returned.

## 9. Security notes

Approval records store only sanitized, non-secret data and an `auth_ref`
reference — never access tokens, MCP session tokens, or raw authorization
headers. Valid credentials are resolved again at resume time. Structured logs use
identifiers and safe metadata (`run_id`, `approval_id`, `tool_call_id`,
`execution_id`, `decision`, `category`, …), never argument bodies.

## 10. Known limitations / decisions

- **Single-node re-execution.** With the current one-node-per-graph-node design,
  resuming replays the node from its start (LangGraph semantics). Preparation is
  deterministic and the idempotency ledger prevents duplicate side effects; a
  future optimization is to split the agent turn into separate LLM/tools nodes so
  only the tools node replays.
- **Shared-DB repositories.** The in-memory `Run`/`Approval`/`ToolExecution`
  repositories back local development. Multi-Pod resume requires a shared
  persistent checkpointer (PostgreSQL) and shared DB-backed repositories that
  implement the same contracts with conditional-update claim semantics.
- **`EDIT_AND_APPROVE`** is modeled (`ApprovalDecisionKind`) but not yet wired;
  the domain is shaped so it can be added without a refactor.
