# ADR 0010: Runtime hooks are a separate concept from tools

- **Status:** Accepted
- **Date:** 2026-06-19
- **Related:** [RUNTIME_HOOKS.md](../RUNTIME_HOOKS.md),
  [MCP_AND_TOOLS.md](../MCP_AND_TOOLS.md),
  [ADR 0001](0001-runtime-engine-created-once.md),
  [ADR 0003](0003-client-specific-logic-lives-in-sidecar.md)

## Context

Organizations embed the platform inside their own application, which already
authenticates users. They need to run custom code at fixed runtime points —
inject auth headers into private MCP calls, exchange an app token for an
MCP-scoped token, sign requests with HMAC, enrich runs with identity, audit, and
apply policy — **without** forking core or writing per-MCP-server client code.

The tempting shortcut is to model these as tools. That is wrong: a tool is
something the **LLM chooses to call** and sees in its prompt. Auth, policy, and
audit must run automatically, must never be exposed to the model, and must never
be selectable by it.

## Decision

Introduce **runtime hooks** as a first-class concept distinct from tools, run by
a `HookManager` at five MVP lifecycle points: `on_engine_start`, `on_run_start`,
`before_mcp_request`, `after_tool_call`, `on_run_error`.

- Hooks are **never** exposed to the LLM, advertised as tools, or selectable by
  the model. They do not pass through `ToolRegistry`.
- Hooks are declared in a top-level YAML `hooks:` section and loaded by import
  path (`module:attribute`) from the host application's environment.
- Hooks receive purpose-built, immutable context models (`RunContext`,
  `McpRequestContext`, `ToolCallContext`, ...), never raw graph state.
- Per-request identity flows through a `contextvars.ContextVar`, not engine
  fields, preserving the once-built, stateless engine of ADR 0001.
- `before_mcp_request` integrates at the existing `httpx.Auth` transport seam
  (the same one `MCPAuthLoader` uses), so enterprise MCP auth needs no core
  changes and no per-server client code.
- Errors are **fail-closed** by default (a `before_mcp_request` failure fails
  the MCP request); a per-hook `failure_policy: warn` allows best-effort audit
  hooks. `on_run_error` hook failures are logged and never mask the original
  error.

## Consequences

**Positive**

- Clean separation: the model's surface (tools) is unchanged; cross-cutting
  concerns (auth/policy/audit) live in hooks.
- Enterprises integrate their own auth/security infrastructure without forking.
- Tokens never reach the LLM or prompts; secrets stay in env/secret managers.
- The engine remains built-once and stateless under concurrency.

**Negative / constraints**

- Hooks are **trusted code** executed in-process; this is not a sandbox for
  untrusted third-party code.
- At the HTTP transport layer the JSON-RPC `operation`/`tool_name` are not
  cheaply available, so `before_mcp_request` sees `operation="request"` and
  headers apply to every MCP operation.
- Hook authors must not log secrets; the platform logs config **keys only**.

## Alternatives considered

- **Model auth as tools.** Rejected: exposes credentials-bearing operations to
  the LLM and lets the model decide whether auth happens.
- **Per-server client code in core.** Rejected: does not scale to many private
  MCP servers and pushes client-specific logic into the platform (counter to
  ADR 0003).
- **Request-scoped engine carrying identity.** Rejected: violates ADR 0001 and
  leaks state across concurrent requests.

## Enforcement

- Hooks are loaded only via `HookManager`; they are never registered as tools or
  bound to a model.
- Tests assert hooks are not exposed to the LLM, fire at the right lifecycle
  points, fail closed, and never log secret values.
