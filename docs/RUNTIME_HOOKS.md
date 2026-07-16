# Runtime Hooks

Runtime hooks let an organization plug **trusted application code** into fixed
points of the runtime lifecycle — for authentication, policy, audit, and context
enrichment — **without forking the platform or writing per-server client code**.

This is the integration seam for embedding the platform inside a private
application that already authenticates its users.

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

## Lifecycle points

Ten points across the engine, run, tool, and MCP lifecycles. Each row lists its
payload (the `HookInvocation.payload` type, or the positional payload arg in
explicit-ref mode) and whether a returned value is used.

| Point | When it runs | Payload | Returns |
|---|---|---|---|
| `on_engine_start` | once, during `Engine.build()` | `EngineContext` | ignored |
| `on_engine_stop` | once, during `Engine.close()` (best-effort) | `EngineContext` | ignored |
| `on_run_start` | once per request, before graph execution | `RunContext` | updated `RunContext` (or `None`) |
| `on_run_end` | once per request, on **successful** completion | `RunEndContext` | ignored |
| `on_run_error` | when a run fails | the `BaseException` | ignored (never masks the error) |
| `before_tool_call` | before every local or MCP tool call | `ToolRequestContext` | ignored (observe-only) |
| `after_tool_call` | after a local or MCP tool call **succeeds** | `ToolCallContext` (status `succeeded`) | ignored |
| `transform_tool_result` | after a tool **succeeds**, before its result is appended to the conversation | `ToolResultContext` (carries the `result`) | updated `ToolResultContext` (or `None`) |
| `on_tool_error` | when a local or MCP tool call **fails** | `ToolCallContext` (status `failed`) | ignored |
| `before_mcp_request` | before every outgoing MCP HTTP request | `McpRequestContext` | updated `McpRequestContext` (or `None`) |
| `after_mcp_response` | after every MCP HTTP response | `McpResponseContext` | ignored (observe-only) |

There are deliberately no `before_everything` / `after_everything` catch-alls.

### Not implemented (no clean seam)

These are intentionally **not** provided, to avoid fake/unreliable hooks:

- **`on_mcp_error`** — at the `httpx.Auth` seam, when the transport itself raises
  (connection error) httpx propagates the exception *without* throwing it back
  into the auth flow, so it is not observable there. MCP **tool-call** failures
  are instead surfaced via `on_tool_error` (with `provider="mcp"`). HTTP error
  *responses* (4xx/5xx) are observable via `after_mcp_response.status_code`.
- **`before_llm_call` / `after_llm_call` / `on_llm_error`** — the model-call seam
  (`helpers.invoke_model`) is reachable, but wiring hooks there requires threading
  the manager through the node→helper call chain, and prompt/response payloads
  risk leaking content. Deferred until a content-safe seam is designed.
- **`before_agent_node` / `after_agent_node` / `on_agent_node_error`** — no stable
  tested LangGraph node seam is needed yet; omitted to avoid overcomplication.

### MCP HTTP-layer limitation

At the HTTP layer the JSON-RPC `operation`/`tool_name` live inside the request
body, so `McpRequestContext.operation` and `McpResponseContext.operation` default
to `"request"` and `tool_name` is `None`. Per-tool attribution is available at the
tool seam instead (`ToolCallContext`/`ToolRequestContext` carry `server_id`). The
response hook never reads or logs the body or headers — only `status_code` and
`latency_ms`.

---

## YAML declaration

Add a top-level `hooks:` section. Each point maps to an **ordered list** of
entries; hooks run in declaration order.

```yaml
hooks:
  on_engine_start:
    - plugin: "mcp_auth"
      method: "validate_auth_setup"

  on_run_start:
    - plugin: "mcp_auth"
      method: "attach_user_context"

  before_mcp_request:
    - plugin: "mcp_auth"
      method: "before_mcp_request"

  after_tool_call:
    - plugin: "mcp_auth"
      method: "record_tool_call"
      failure_policy: warn   # best-effort audit: log and continue on failure

  on_run_error:
    - plugin: "mcp_auth"
      method: "record_run_failure"
```

### Entry fields

- **`plugin`** + **`method`** *(preferred)* — logical plugin id and method name.
  The id is resolved through `plugins/plugins.toml` `[hooks.plugins]`.
- **`ref`** *(advanced / backwards compatible)* — explicit import path to the
  hook callable. Use either `ref` or `plugin` + `method`, never both.
- **`failure_policy`** *(optional, `fail` | `warn`, default `fail`)* — `fail`
  aborts the operation (fail-closed); `warn` logs and continues.

### Validation

Loading is fail-closed. The YAML is rejected if a hook point is unknown, a hook
entry does not use exactly one of `ref` or `plugin` + `method`, declares
implementation-specific hook settings, or a `failure_policy` is not
`fail`/`warn`. A `ref` that cannot be imported or a plugin id that cannot be
resolved aborts `Engine.build()` before any request is served.

---

## Managed Plugin Mode

Generated/client-owned hooks should use managed plugin mode:

```yaml
hooks:
  before_mcp_request:
    - plugin: "mcp_auth"
      method: "before_mcp_request"
```

The plugin id is resolved through `plugins/plugins.toml`:

```toml
[hooks.plugins]
mcp_auth = "plugins.hooks.mcp_auth:McpAuthHook"
```

The hook class is instantiated once when `HookManager` is built. The same
instance is reused for every hook entry that references the same plugin id,
including different hook points. Hook methods receive a single
`HookInvocation` object.

```python
import os
from agent_engine.runtime.hooks import HookInvocation, McpRequestContext

class McpAuthHook:
    _REQUIRED_ENV = ("INTERNAL_MCP_BEARER", "INTERNAL_MCP_TENANT")

    def __init__(self) -> None:
        self._cache: dict[str, object] = {}

    def validate_auth_setup(self, event: HookInvocation) -> None:
        missing = [name for name in self._REQUIRED_ENV if not os.environ.get(name)]
        if missing:
            raise RuntimeError(f"Missing MCP auth environment: {', '.join(missing)}")

    async def before_mcp_request(self, event: HookInvocation) -> McpRequestContext:
        request = event.payload_as(McpRequestContext)
        bearer = os.environ["INTERNAL_MCP_BEARER"]
        tenant = os.environ["INTERNAL_MCP_TENANT"]
        return request.with_headers(
            {
                "Authorization": f"Bearer {bearer}",
                "X-Tenant": tenant,
            }
        )
```

`HookInvocation` contains:

- `hook_point`
- `plugin`, `method`, `ref`
- `run_context`
- `payload`
- `metadata`

The `payload` is hook-specific: `EngineContext` for `on_engine_start`,
`RunContext` for `on_run_start`, `McpRequestContext` for `before_mcp_request`,
`ToolCallContext` for `after_tool_call`, `ToolResultContext` for
`transform_tool_result`, and the exception for `on_run_error`.

Class-hook instances may keep safe long-lived state such as initialized clients,
tenant metadata, keyed caches, or audit/metrics clients. They must not
store unsafe per-request state such as the current user, organization, inbound
token, request object, headers, or last request; that data must come from
`event.run_context` and `event.payload` on each call. If a cache stores user- or
tenant-derived data, key it safely and make it concurrency-safe.

## Explicit `ref` Mode

Explicit refs remain supported for advanced/manual integrations and backwards
compatibility. Canonical form is **`module.path:attribute`**; the colon makes
the module/attribute split unambiguous. The dotted form
`module.path.attribute` is also accepted.

A `ref` may point to:

- a function (sync **or** async);
- a callable object;
- a class — it is instantiated once with no arguments and the instance (which
  must be callable) is used.
- a class method, written as `module.path:Class.method`. The class is
  instantiated while the hook manager is built for that hook entry.

The loader imports normal Python modules with `importlib.import_module`, so
hooks live in an **importable package** — unlike tools and resolvers, which the
runtime loads by *file path* from `plugins/tools/` and `plugins/resolvers/`
relative to the agent YAML. Hooks are referenced by import path because they are
the integration seam for code the embedding application already owns.

### Making your hooks importable

A package-path `ref` only resolves if its top-level package is on `sys.path`.
**Do not rely on the current working directory** — launching the CLI from another
directory would otherwise break the import.

The robust, recommended way is to declare **plugin import roots** in the spec:

```yaml
plugins:
  import_roots:
    - "."         # resolved relative to THIS YAML file, not the shell's CWD
```

The engine resolves each root relative to the agent YAML's location and
registers it on `sys.path` **before importing any plugin**, exactly once. In the
flagship example, `examples/enterprise-knowledge-assistant/agents.yaml` sits
next to its own `plugins/` package, so `"."` resolves to that directory — and
`plugins.hooks.research_hooks:...` refs then import no matter where
`agentctl` was launched from. **No `PYTHONPATH` needed.**

Notes:

- Roots are resolved against the YAML file, never the CWD. A missing root fails
  with a clear error; duplicate roots are de-duplicated.
- With no `import_roots` declared, nothing is added to `sys.path` (behavior is
  unchanged) — useful when your package is already installed (`pip install -e .`)
  or otherwise importable.
- This is the single, centralized place that touches `sys.path` for plugins; do
  not scatter manual `sys.path` edits. (The API server additionally inserts the
  config's own directory, as before.)

All client extension code lives under **one** plugin package so resolvers,
tools, and hooks sit together, described by **one** manifest:

```
enterprise-knowledge-assistant/
  agents.yaml                    # agent spec (NOT inside the Python package)
  plugins/
    __init__.py
    plugins.toml                 # maps managed hook ids; catalogs other refs
    resolvers/   __init__.py …   # loaded by file path
    tools/       __init__.py …   # loaded by file path
    hooks/       __init__.py
      research_hooks.py          # loaded by import path
```

Hooks, resolvers, and tools remain separate runtime concepts but share one
package and one manifest. The agent YAML stays **outside** the importable Python
package (app config is not plugin code). Managed hook YAML uses plugin ids such
as `research_hooks`, while `plugins.toml` maps those ids to importable class paths.

Because the example declares `plugins.import_roots: ["."]`, these refs resolve
from any working directory — `plugins/` and `plugins/hooks/` are packages, and
the spec's own directory is registered on `sys.path` at build time. For your own
project, declare an `import_roots` entry (or install the package with
`pip install -e .`).

### The `plugins.toml` manifest

`examples/enterprise-knowledge-assistant/plugins/plugins.toml` is a single manifest for **all** client
extension code — `[hooks]`, `[resolvers]`, `[tools]` (plus `[package]` and
`[paths]`). There is intentionally **no** per-type file (`hooks.toml`,
`resolvers.toml`, `tools.toml`).

It is a **documentation / generation artifact** except for `[hooks.plugins]`,
which the runtime reads to resolve managed hook plugin ids to importable class
paths. Explicit hook refs still load directly from YAML; resolvers and tools load
by file path. `agentctl generate` creates the manifest automatically if missing
and **merges** new entries into it on each run — existing entries are preserved
(never overwritten without an explicit force), duplicates are not added, and
output is deterministic. It must never contain secrets — only import refs and
metadata.

---

## Hook Signatures And Returns

The manager bridges sync and async uniformly — a hook may be a plain `def` or an
`async def`. Managed plugin methods receive one `HookInvocation` object.
Explicit refs keep the historical positional signatures:

```python
def on_engine_start(context: EngineContext) -> None: ...

def on_run_start(context: RunContext) -> RunContext | None: ...

def before_mcp_request(
    context: RunContext | None, request: McpRequestContext
) -> McpRequestContext | None: ...

def after_tool_call(
    context: RunContext | None, call: ToolCallContext
) -> None: ...

def transform_tool_result(
    context: RunContext | None, result: ToolResultContext
) -> ToolResultContext | None: ...

def on_run_error(
    context: RunContext | None, error: BaseException
) -> None: ...
```

Return values are interpreted by hook point: `on_run_start` may return an
updated `RunContext`; `before_mcp_request` may return an updated
`McpRequestContext`; `transform_tool_result` may return an updated
`ToolResultContext`; `None` keeps the original object. **All other points are
observe-only — their return value is ignored.** (Mutating a pending tool call or
an MCP response is not supported in this version.)

In **managed plugin mode**, a method receives a single `HookInvocation` whose
`.payload` is the same context object (use `event.payload_as(ToolCallContext)`
for a typed view) and whose `.run_context` carries the active `RunContext`.

### Context models

```python
RunContext(run_id, conversation_id, user_id, organization_id, metadata, auth_context)
AuthContext(user_id, organization_id, inbound_access_token, scopes, roles, metadata)
EngineContext(system_name, metadata)
RunEndContext(run_id, system_name, status, visited, used_tool_count, metadata)
ToolRequestContext(agent_id, tool_name, provider, server_id, metadata)
ToolCallContext(agent_id, tool_name, provider, server_id, status, latency_ms, error, metadata)
ToolResultContext(agent_id, tool_name, provider, result, server_id, latency_ms, metadata)
McpRequestContext(server_id, url, operation, tool_name, headers, metadata)
McpResponseContext(server_id, url, status_code, operation, tool_name, latency_ms, metadata)
```

All are frozen dataclasses. `RunContext.replace(**changes)` and
`McpRequestContext.with_headers({...})` and `ToolResultContext.with_result(...)`
return updated copies; the `headers` and `metadata` dicts may also be mutated in
place. Hooks never receive raw graph
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
reach the active identity without the shared engine storing request state.

---

## Enterprise MCP auth — the key use case

A private application authenticates the user, then calls the runtime. Before the
runtime calls a private MCP server, a `before_mcp_request` hook adds the right
credentials. **The LLM never sees the token; the user never sees MCP internals;
core needs no per-server code.**

### Add auth headers

```python
import os
from agent_engine.runtime.hooks import HookInvocation, McpRequestContext

class McpAuthHook:
    async def before_mcp_request(self, event: HookInvocation) -> McpRequestContext:
        request = event.payload_as(McpRequestContext)
        bearer = os.environ["INTERNAL_MCP_BEARER"]
        tenant = os.environ["INTERNAL_MCP_TENANT"]
        return request.with_headers(
            {
                "Authorization": f"Bearer {bearer}",
                "X-Tenant": tenant,
            }
        )
```

### Exchange the inbound token for an MCP-scoped token

```python
class McpAuthHook:
    async def before_mcp_request(self, event):
        request = event.payload_as(McpRequestContext)
        inbound = event.run_context.auth_context.inbound_access_token if event.run_context else None
        credential = exchange_token(inbound, audience="internal-mcp")
        request.headers["Authorization"] = f"Bearer {credential}"
        return request
```

### Sign the request with HMAC

```python
import hashlib, hmac, os, time

def sign_mcp_request(context, request):
    secret = os.environ["INTERNAL_MCP_HMAC_KEY"].encode()
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
| `on_engine_stop` | logged; **cleanup still proceeds** (never raised) |
| `on_run_start` | the run fails |
| `on_run_end` | the run's success path fails |
| `before_tool_call` | the run fails (a policy gate can block the call) |
| `after_tool_call` | the run fails |
| `transform_tool_result` | the run fails (use `warn` to pass the original result through) |
| `on_tool_error` | the run fails |
| `before_mcp_request` | the MCP request fails |
| `after_mcp_response` | the MCP request fails |
| `on_run_error` | logged; **the original run error is preserved** |

Use `failure_policy: warn` for best-effort hooks (e.g. audit) that should log
and continue instead of aborting. Hook errors are never silently swallowed:
they are logged with their point and `ref`, and (except `on_engine_stop` and
`on_run_error`, which are best-effort) raised.

---

## Logging and no-op behavior

The runtime emits structured logs for meaningful hook actions and failures:

- **Transforming hooks** (`on_run_start`, `before_mcp_request`, and
  `transform_tool_result`): `HookManager` emits `hook applied` at INFO only when
  the effective context changed, including an in-place mutation. A no-op is
  completely silent; there are no per-invocation `start` or `done` logs at any
  level.
- **Observe-only hooks**: the manager emits no success log because a `None`
  return cannot prove that an external audit or metric action occurred. The hook
  implementation should emit its own safe INFO event only after that action
  succeeds.
- **Failures**: `HookManager` emits `hook failed` at ERROR with point, ref,
  duration, policy, and exception type. Exception messages and payload values
  are omitted because they may contain secrets.
- Manager initialization and hook loading remain startup diagnostics. An empty
  manager logs its zero count only at DEBUG. Config **keys only** are logged at
  DEBUG — never values.
- **Runtime stages**: `system ready`, `engine stopping`, `run started`,
  `run ended`, `run failed`, `tool call started` / `ended` / `failed` (with
  `latency_ms`), and at DEBUG `before_mcp_request applied headers` /
  `after_mcp_response` (status + latency, count of applied headers).

Never logged: Authorization headers, tokens, refresh tokens, HMAC secrets or
signatures, raw prompts, raw tool arguments/results, or raw
MCP request/response bodies.

**Hooks are fully optional.** With no `hooks:` section, `hooks: {}`, no
`plugins.toml`, or a `plugins.toml` without `[hooks.plugins]`, the engine still
builds a `HookManager` whose `hook_count == 0`: every `run_*` method is a safe
no-op, no plugin manifest is read, and no hook import is attempted. The public
DeepWiki/no-hook examples build and run unchanged.

---

## Security model

**Hooks are trusted application code, executed in-process. This is not a sandbox
for untrusted third-party code.**

Because hooks are trusted code, `agentctl validate` (like the engine's build)
**imports** hook refs and **instantiates** class/plugin hooks to confirm they
resolve — it never calls hook *methods*. `agentctl inspect` shows hook identity
and failure policy, not hook runtime data.

The platform never logs, and your hooks must never log, Authorization headers,
HMAC signatures, or any inbound access token.

Secrets come from environment variables or your organization's secret manager,
**resolved inside hook code** — never inlined in YAML. The YAML secret scanner
rejects secret-like keys/values (including in the `hooks:` section): any string
containing `token`, `secret`, `password`, `api_key`, or `private_key` is
rejected. If a hook needs configuration, put that logic in the hook
implementation. Hook outputs are never placed into prompts.

---

## Out of scope (MVP)

Full OAuth, a token store, secret-manager integration, an RBAC/policy DSL,
approval workflows, hook sandboxing, running untrusted hooks safely, and
distributed hook execution are all out of scope. `after_tool_call` is
side-effect only and does not alter tool results — use `transform_tool_result`
to reshape a result (e.g. truncate oversized MCP output) before it reaches the
conversation.
