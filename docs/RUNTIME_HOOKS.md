# Runtime Hooks

Runtime hooks let an organization plug **trusted application code** into fixed
points of the runtime lifecycle — for authentication, policy, audit, and context
enrichment — **without forking the platform or writing per-server client code**.

This is the integration seam for embedding the platform inside a private
application that already authenticates its users.

> See [ADR 0010](adr/0010-runtime-hooks.md) for the binding decision and
> rationale.

---

## Tools vs. hooks — they are different things

| | Tool | Hook |
|---|---|---|
| Who invokes it | the **LLM** chooses to call it | the **runtime** runs it automatically |
| Visible to the model | yes (name + description in tool metadata) | **no — never** |
| Selected by the model | yes | no |
| Purpose | do work the model asked for | auth, policy, audit, context enrichment |
| Routed through | `ToolRegistry` / agent tool binding | `HookManager` |

Hooks are **not** exposed in prompts, are **not** advertised as tools, and the
model can neither name nor call them. A tool answers a model's request; a hook
runs on the runtime's own schedule.

---

## Lifecycle points (MVP)

| Point | When it runs | Typical use | May return |
|---|---|---|---|
| `on_engine_start` | once, during `Engine.build()` | load tenant config, init clients, validate auth setup | — |
| `on_run_start` | once per request, before graph execution | attach user/org/auth context, enrich metadata, audit start | updated `RunContext` |
| `before_mcp_request` | before every outgoing MCP HTTP request | add `Authorization`, exchange tokens, sign with HMAC, add tenant/correlation headers | updated `McpRequestContext` |
| `after_tool_call` | after any local or MCP tool call completes | audit, metrics, external logging | — (side-effect only) |
| `on_run_error` | when a run fails | audit failure, cleanup, notify monitoring | — (side-effect only) |

There are deliberately no `before_everything` / `after_everything` catch-alls.

---

## YAML declaration

Add a top-level `hooks:` section. Each point maps to an **ordered list** of
entries; hooks run in declaration order.

```yaml
hooks:
  on_engine_start:
    - ref: "company.plugins.bootstrap:load_tenant_config"

  on_run_start:
    - ref: "company.plugins.auth:attach_user_context"

  before_mcp_request:
    - ref: "company.plugins.auth:add_mcp_auth_headers"
      config:
        audience: "internal-docs-mcp"
        token_exchange: true

  after_tool_call:
    - ref: "company.plugins.audit:record_tool_call"
      failure_policy: warn   # best-effort audit: log and continue on failure

  on_run_error:
    - ref: "company.plugins.audit:record_run_failure"
```

### Entry fields

- **`ref`** *(required, string)* — import path to the hook callable.
- **`config`** *(optional, mapping)* — opaque settings passed to the hook on
  every call. **Do not put secrets here** (see Security).
- **`failure_policy`** *(optional, `fail` | `warn`, default `fail`)* — `fail`
  aborts the operation (fail-closed); `warn` logs and continues.

### Validation

Loading is fail-closed. The config is rejected if a hook point is unknown, a
`ref` is missing or not a string, a `config` is not a mapping, or a
`failure_policy` is not `fail`/`warn`. A `ref` that cannot be imported or is not
callable aborts `Engine.build()` before any request is served.

---

## The `ref` format

Canonical form is **`module.path:attribute`** — the colon makes the
module/attribute split unambiguous. The dotted form `module.path.attribute` is
also accepted.

A `ref` may point to:

- a function (sync **or** async);
- a callable object;
- a class — it is instantiated once with no arguments and the instance (which
  must be callable) is used.

The loader imports normal Python modules from the host application's
environment, so hooks live in **your** package — they are not file-path plugins
under `plugins/` like tools and resolvers.

---

## Hook signatures

The manager bridges sync and async uniformly — a hook may be a plain `def` or an
`async def`.

```python
def on_engine_start(context: EngineContext, config: dict) -> None: ...

def on_run_start(context: RunContext, config: dict) -> RunContext | None: ...

def before_mcp_request(
    context: RunContext | None, request: McpRequestContext, config: dict
) -> McpRequestContext | None: ...

def after_tool_call(
    context: RunContext | None, call: ToolCallContext, config: dict
) -> None: ...

def on_run_error(
    context: RunContext | None, error: BaseException, config: dict
) -> None: ...
```

Convention: lifecycle hooks that own a single context get `(context, config)`;
event hooks that fire *within* a run get `(run_context, event, config)` so they
can read the active identity while acting on the event.

### Context models

```python
RunContext(run_id, conversation_id, user_id, organization_id, metadata, auth_context)
AuthContext(user_id, organization_id, inbound_access_token, scopes, roles, metadata)
McpRequestContext(server_id, url, operation, tool_name, headers, metadata)
ToolCallContext(agent_id, tool_name, provider, server_id, status, latency_ms, error, metadata)
EngineContext(system_name, metadata)
```

All are frozen dataclasses. `RunContext.replace(**changes)` and
`McpRequestContext.with_headers({...})` return updated copies; the `headers` and
`metadata` dicts may also be mutated in place. Hooks never receive raw graph
state.

---

## Passing identity into a run

The embedding application supplies per-request identity through `RunContext`:

```python
from agent_engine.runtime.hooks import AuthContext, RunContext

ctx = RunContext(
    user_id="u-123",
    organization_id="org-7",
    auth_context=AuthContext(inbound_access_token=request_token, scopes=("docs:read",)),
)
result = await engine.run(message, context=ctx)
```

`engine.run(message)` with no context stays fully backwards compatible — a
`RunContext` with an auto-generated `run_id` is created internally. During the
run the context is held in a `contextvars.ContextVar`, so MCP and tool hooks
reach the active identity without the shared engine storing request state
(per [ADR 0001](adr/0001-runtime-engine-created-once.md)).

---

## Enterprise MCP auth — the key use case

A private application authenticates the user, then calls the runtime. Before the
runtime calls a private MCP server, a `before_mcp_request` hook adds the right
credentials. **The LLM never sees the token; the user never sees MCP internals;
core needs no per-server code.**

### Add an Authorization header

```python
import os
from agent_engine.runtime.hooks import McpRequestContext, RunContext

def add_static_auth_header(
    context: RunContext | None, request: McpRequestContext, config: dict
) -> McpRequestContext:
    token = os.environ[config["credential_env"]]     # secret from env, not YAML
    return request.with_headers({"Authorization": f"Bearer {token}"})
```

### Exchange the inbound token for an MCP-scoped token

```python
def add_mcp_auth_headers(context, request, config):
    inbound = context.auth_context.inbound_access_token if context else None
    token = exchange_token(inbound, audience=config["audience"])  # your code
    request.headers["Authorization"] = f"Bearer {token}"
    return request
```

### Sign the request with HMAC

```python
import hashlib, hmac, os, time

def sign_mcp_request(context, request, config):
    secret = os.environ[config["hmac_key_env"]].encode()
    ts = str(int(time.time()))
    mac = hmac.new(secret, f"{ts}:{request.url}".encode(), hashlib.sha256).hexdigest()
    return request.with_headers({"X-Timestamp": ts, "X-Signature": mac})
```

### How it is wired

When any `before_mcp_request` hook is declared, the engine wraps each MCP
server's transport auth with a hook-driven `httpx.Auth`. On every MCP HTTP
request it builds an `McpRequestContext`, runs the hooks, and applies the
returned headers. If a per-MCP `plugins/mcp_auth/{id}.py` auth also exists, it
runs first and hooks add on top.

**Transport limitation:** at the HTTP layer the JSON-RPC `operation`/`tool_name`
live inside the request body and are not cheaply available, so `operation`
defaults to `"request"`. Headers are applied for **every** MCP operation
(connect, list_tools, call_tool), which is exactly what enterprise auth needs.
At `Engine.build()` time (initial connect / tool discovery) there is no active
run, so `context` is `None`; hooks needing per-request identity should guard for
that and rely on env/config-based service credentials for the discovery phase.

---

## Error policy

Fail-closed by default — security hooks must not be silently skipped:

| Point | On hook failure (`failure_policy: fail`) |
|---|---|
| `on_engine_start` | `Engine.build()` fails |
| `on_run_start` | the run fails |
| `before_mcp_request` | the MCP request fails |
| `after_tool_call` | the run fails |
| `on_run_error` | logged; **the original run error is preserved** |

Use `failure_policy: warn` for best-effort hooks (e.g. audit) that should log
and continue instead of aborting. Hook errors are never silently swallowed:
they are logged with their point and `ref`, and (except `on_run_error`) raised.

---

## Security model

**Hooks are trusted application code, executed in-process. This is not a sandbox
for untrusted third-party code.**

The platform never logs, and your hooks must never log:

- Authorization headers, HMAC signatures, or any inbound access token;
- hook **config values** — the manager logs config **keys only**, at DEBUG.

Secrets come from environment variables or your organization's secret manager,
**resolved inside hook code** — never inlined in YAML. The YAML secret scanner
rejects secret-like keys/values (including in the `hooks:` section): any string
containing `token`, `secret`, `password`, `api_key`, or `private_key` is
rejected, **even as a plain env-var reference**. Name your env-var references
without those words — e.g. `credential_env: INTERNAL_MCP_CREDENTIAL` rather than
`token_env: INTERNAL_MCP_TOKEN`. Hook outputs are never placed into prompts.

---

## Out of scope (MVP)

Full OAuth, a token store, secret-manager integration, an RBAC/policy DSL,
approval workflows, hook sandboxing, running untrusted hooks safely, and
distributed hook execution are all out of scope. `after_tool_call` is
side-effect only and does not alter tool results.
