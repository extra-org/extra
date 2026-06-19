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
    - plugin: "mcp_auth"
      method: "validate_auth_setup"
      config:
        required_env: ["INTERNAL_MCP_CREDENTIAL"]

  on_run_start:
    - plugin: "mcp_auth"
      method: "attach_user_context"

  before_mcp_request:
    - plugin: "mcp_auth"
      method: "before_mcp_request"
      config:
        credential_env: "INTERNAL_MCP_CREDENTIAL"

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
- **`config`** *(optional, mapping)* — opaque settings passed to the hook on
  that invocation as `HookInvocation.config`. **Do not put secrets here** (see
  Security).
- **`failure_policy`** *(optional, `fail` | `warn`, default `fail`)* — `fail`
  aborts the operation (fail-closed); `warn` logs and continues.

### Validation

Loading is fail-closed. The config is rejected if a hook point is unknown, a hook
entry does not use exactly one of `ref` or `plugin` + `method`, a `config` is not
a mapping, or a `failure_policy` is not `fail`/`warn`. A `ref` that cannot be
imported or a plugin id that cannot be resolved aborts `Engine.build()` before
any request is served.

---

## Managed Plugin Mode

Generated/client-owned hooks should use managed plugin mode:

```yaml
hooks:
  before_mcp_request:
    - plugin: "mcp_auth"
      method: "before_mcp_request"
      config:
        credential_env: "INTERNAL_MCP_CREDENTIAL"
```

The plugin id is resolved through `plugins/plugins.toml`:

```toml
[hooks.plugins]
mcp_auth = "examples.plugins.hooks.mcp_auth:McpAuthHook"
```

The hook class is instantiated once when `HookManager` is built. The same
instance is reused for every hook entry that references the same plugin id,
including different hook points. Hook methods receive a single
`HookInvocation` object.

```python
class McpAuthHook:
    def __init__(self, config: object | None = None) -> None:
        self.config = config
        self._cache: dict[str, object] = {}

    async def before_mcp_request(self, event: HookInvocation) -> object:
        request = event.payload_as(McpRequestContext)
        credential_env = dict(event.config or {})["credential_env"]
        credential = os.environ[credential_env]
        return request.with_headers({"Authorization": f"Bearer {credential}"})
```

`HookInvocation` contains:

- `hook_point`
- `plugin`, `method`, `ref`
- `run_context`
- `payload`
- `config`
- `metadata`

The `payload` is hook-specific: `EngineContext` for `on_engine_start`,
`RunContext` for `on_run_start`, `McpRequestContext` for `before_mcp_request`,
`ToolCallContext` for `after_tool_call`, and the exception for `on_run_error`.

Class-hook instances may keep safe long-lived state such as config, initialized
clients, tenant metadata, keyed caches, or audit/metrics clients. They must not
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
    - ".."        # resolved relative to THIS YAML file, not the shell's CWD
```

The engine resolves each root relative to the agent YAML's location and
registers it on `sys.path` **before importing any plugin**, exactly once. In the
bundled example, `examples/hooks_mcp_auth_agents.yml` sits in `examples/`, so
`".."` is the repo root (which holds the `examples` package) — and
`examples.plugins.hooks.mcp_auth:...` refs then import no matter where
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
examples/
  hooks_mcp_auth_agents.yml      # agent spec (NOT inside the Python package)
  plugins/
    __init__.py
    plugins.toml                 # maps managed hook ids; catalogs other refs
    resolvers/   __init__.py …   # loaded by file path
    tools/       __init__.py …   # loaded by file path
    hooks/       __init__.py
      mcp_auth.py                # loaded by import path
```

Hooks, resolvers, and tools remain separate runtime concepts but share one
package and one manifest. The agent YAML stays **outside** the importable Python
package (app config is not plugin code). Managed hook YAML uses plugin ids such
as `mcp_auth`, while `plugins.toml` maps those ids to importable class paths.

Because the example declares `plugins.import_roots: [".."]`, these refs resolve
from any working directory — `examples/`, `examples/plugins/`, and
`examples/plugins/hooks/` are packages, and the repo root is registered on
`sys.path` at build time. For your own project, declare an `import_roots` entry
(or install the package with `pip install -e .`).

### The `plugins.toml` manifest

`examples/plugins/plugins.toml` is a single manifest for **all** client
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

Return values are interpreted by hook point: `on_run_start` may return an
updated `RunContext`; `before_mcp_request` may return an updated
`McpRequestContext`; `None` keeps the original object. Return values from
`on_engine_start`, `after_tool_call`, and `on_run_error` are ignored.

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
from agent_engine.runtime.hooks import HookInvocation, McpRequestContext

class McpAuthHook:
    def __init__(self, config: object | None = None) -> None:
        self.config = config

    async def before_mcp_request(self, event: HookInvocation) -> McpRequestContext:
        request = event.payload_as(McpRequestContext)
        credential = os.environ[dict(event.config or {})["credential_env"]]
        return request.with_headers({"Authorization": f"Bearer {credential}"})
```

### Exchange the inbound token for an MCP-scoped token

```python
class McpAuthHook:
    def __init__(self, config: object | None = None) -> None:
        self.config = config

    async def before_mcp_request(self, event):
        request = event.payload_as(McpRequestContext)
        inbound = event.run_context.auth_context.inbound_access_token if event.run_context else None
        credential = exchange_token(inbound, audience=dict(event.config or {})["audience"])
        request.headers["Authorization"] = f"Bearer {credential}"
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
