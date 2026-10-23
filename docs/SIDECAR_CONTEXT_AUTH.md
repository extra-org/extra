# Plugin Context & Access

This document defines the current context and access model. The filename is
kept for link stability, but the current MVP design is plugin-based rather than
sidecar-first.

Client-specific authentication, authorization, database access, REST clients,
and business context live in client Python plugins. The engine remains generic:
it loads plugins, calls fixed methods, maps results into request execution, and
traces decisions.

---

## Context

The engine is invoked with a complete conversation and request headers:

```http
POST /api/invoke
Authorization: Bearer <token>
Content-Type: application/json

{
  "messages": [{ "role": "user", "content": "..." }]
}
```

For each request, the runtime builds `ctx` from headers and request data. Plugin
methods receive `ctx`; client code decides how to interpret tokens, user ids,
tenant ids, roles, and permissions. Tools are not passed `ctx` as an argument —
that would put it in the schema the model sees — and read it from
`agent_engine.runtime.hooks.current_run_context` instead.

---

## Credentials Are Accepted, Facts Are Derived

`Authorization` reaches `ctx.auth_context.inbound_access_token` and is forwarded
to plugin code without being verified here. That is deliberate, and the split it
rests on is the one rule to keep in mind:

- **A credential is self-securing.** Forge it and the system that owns it answers
  401. Nothing can reach through this engine that it could not already reach
  directly, so taking one from the caller costs nothing.
- **A fact is not.** `{"role": "admin"}` works the moment anything trusts it.

So a credential may be accepted from the caller; a user id, a role, or a tenant
must be *derived* — a resolver calls the owning system with the credential and
uses what comes back. Nothing else about the caller is accepted over the wire,
which is why `/invoke` takes no context object: there is nothing safe to put in
one that a resolver cannot obtain for itself.

Where identity must be **proven** rather than passed along — conversation
ownership, protected nodes — run behind `agent_manager`, which verifies
signatures before building the context. The engine alone is a conduit; the
manager is an authority. Both produce the same `RunContext`.

The engine guarantees one property, and there is a test pinning it:
`inbound_access_token` never enters graph state, so it cannot reach a prompt,
the model, or a response.

The other half is the plugin author's to keep: a tool that finds no credential
must **fail**, never fall back to a shared key. That fallback is precisely what
lets every caller act as an administrator, and no engine can prevent it for you.

---

## Resolver Plugins

Resolvers fill prompt variables before a node runs. They are deterministic from
the engine's point of view and are not exposed to the LLM.

### YAML declaration

Each resolver has a **scope** (`shared` or `agent`) and a return type:

```yaml
resolvers:
  current_date:
    scope: shared        # generated on BaseResolver, inherited by all nodes
  user_name:
    scope: shared
  experience_level:
    scope: agent         # generated only on the declaring node's subclass

agents:
  learning_planner_agent:
    description: "Produces personalized learning roadmaps and study plans."
    prompts:
      system: "prompts/learning_planner_agent/system.md"
    resolvers: [current_date, user_name, experience_level]
```

### Generated file layout

`agentctl generate` produces a **file-per-class** layout:

```text
plugins/
  __init__.py
  plugins.toml                   # single manifest (hooks + resolvers + tools)
  resolvers/
    __init__.py
    shared.py                    # SharedResolver with shared methods
    documentation_agent.py       # agent-specific Resolver subclass
    repository_agent.py
    learning_planner_agent.py
```

### Manifest entry

Resolver classes are loaded by file path; their importable refs are recorded in
the single unified manifest `plugins/plugins.toml` (one manifest for hooks,
resolvers, and tools — documentation/generation only, not read at runtime):

```toml
[resolvers]
shared = "plugins.resolvers.shared:SharedResolver"
learning_planner_agent = "plugins.resolvers.learning_planner_agent:Resolver"
```

### Client implementation

```python
# plugins/resolvers/shared.py
from agent_engine.runtime.hooks import RunContext

class SharedResolver:
    def current_date(self, ctx: RunContext) -> str:
        return "2026-06-12"

    def user_name(self, ctx: RunContext) -> str:
        return "Jordan"
```

```python
# plugins/resolvers/learning_planner_agent.py
from plugins.resolvers.shared import SharedResolver
from agent_engine.runtime.hooks import RunContext

class Resolver(SharedResolver):
    def experience_level(self, ctx: RunContext) -> str:
        return "intermediate"
```

`Resolver` inherits `current_date` and `user_name` from `SharedResolver`. Only
`experience_level` (agent-scoped) needs an implementation in the child class.

### Generation modes

| Mode | Command | Effect |
| ---- | ------- | ------ |
| `all` | `agentctl generate agents.yml --mode all` | Regenerate base, all agents, TOML |
| `children` | `agentctl generate agents.yml --mode children` | Agent files only; skip base |
| `child` | `agentctl generate agents.yml --mode child --agent learning_planner_agent` | One agent file only |

Use `--force` to overwrite existing files. Without `--force`, existing method
bodies are preserved and only missing stubs are appended. Stale methods (scope
changes, removed resolvers, orphan files) are reported but never deleted
automatically.

### Runtime resolution

1. The runtime loads the node's resolver class from TOML (agents and
   orchestrators alike).
2. The class is instantiated once with configured dependencies.
3. Methods are called by resolver id; shared methods resolve via Python
   inheritance.
4. Outputs land on `ExecutionContext` and are request-scoped.

Missing resolver declarations, missing plugin methods, resolver errors, or
missing variables in strict prompt rendering produce clear errors.

---

## Access Plugin

Authorization is opt-in per node. A child marked `protected: true` is hidden
from its parent unless the fixed access plugin allows it. A protected root
agent or orchestrator passes through the same policy before its executor is
entered, since it has no parent to filter it.

```yaml
agents:
  admin_agent:
    description: "Sensitive administrative information and operations."
    prompts:
      system: "prompts/admin/system.md"
    protected: true
```

If any protected node exists, the engine expects:

```python
# plugins/access.py
class AccessResolver:
    def can_access(self, ctx, node_id: str) -> bool:
        ...
```

Rules:

- Non-protected nodes are never checked and are always reachable.
- Protected nodes are checked before routing.
- If `can_access` returns true, the node remains a routing candidate.
- If `can_access` returns false or raises, the node is hidden.
- On a new run of a protected root, denial returns `Access denied.` with
  an empty visited route and no tool usage. Prompt resolvers, models, and tools
  are not invoked. `/invoke` keeps its normal response shape and HTTP 200;
  `/stream` emits the fixed refusal as an `answer_delta`, then a `final` event
  containing the same refusal and empty route. Delta-only clients such as the
  CLI therefore display the refusal too.
  No model-generated answer or route event is emitted for a denied root.
- `protected: true` without `plugins/access.py` is a configuration error at
  startup, including when a Python host builds the engine directly.
- `plugins/access.py` with no protected nodes is harmless and may be ignored.

The access resolver receives the per-request, non-secret `run_context`
projection for roots and children alike: available run, conversation, user and
organization IDs, metadata, and `auth` facts such as roles and scopes. The
inbound access token is excluded. A Python host supplies trusted `RunContext`
values; `agent_manager` derives them from its verified principal. The thin
engine API accepts no caller-asserted roles or user identity, including forged
`X-User-ID` or `X-Roles` headers.

No universal role requirement is imposed by the engine. If the policy requires
identity or metadata that is missing or malformed, it must deny or raise; both
outcomes fail closed. An explicitly permissive policy can allow an empty
context. A root's decision is evaluated per execution, never cached between
requests. This enforces node access without implementing API authentication or
replacing authorization inside client tools and data services.

Approval replay uses the **current approval request's** trusted context. Python
hosts pass `context=RunContext(...)` to `resume` / `resume_stream`;
`agent_manager` builds it from the verified principal on each decision request,
including that principal's current roles, organization and credential. Supplied
run/user/session identities must match the approval's bound caller and run.
The root wrapper replaces its execution-local context projection before access
checks and child delegation. Saved roles, scopes, organization and metadata are
never merged into it, and credentials are never added to the graph projection.
Omitting fresh permission facts does not recover the old grant. A permissive
policy can still explicitly allow a context with no role or scope facts.

The root policy is rechecked before its executor is entered. If that policy
denies access, the reviewed invocation is never reached. The existing
replay-binding guard raises `ApprovalInvocationMismatch`, rejects the pending
approval and fails the run; the decision is not reported as an executed action.
Removing a protected child can instead trigger the existing checkpoint replay
mismatch guard, which also prevents execution and fails the run. Neither case
re-generates the reviewed model/tool request. A denied resume retains the
existing error handling rather than a new run's normal refusal response.

The optional Python approval ports require implementations to accept the new
`context` keyword; a host must not silently discard it for a legacy adapter.
The HTTP decision body, SSE event types, access-plugin signature and YAML
remain unchanged. See [ADR 0002](adr/0002-conversation-lifecycle-and-immutable-branches.md).

---

## Why Not YAML Roles?

The engine should not invent a role model. Client applications already have
auth and RBAC systems. The access plugin receives the request context and node
id, then asks the client's existing system whether access is allowed.

This keeps adoption fast and avoids baking one company's permission model into
the generic runtime.

---

## Future Sidecar Option

A separate sidecar service may still be useful later for stronger isolation or
non-Python client logic. If reintroduced, it should be treated as another
extension boundary with a design note and updated schema. The current schema
and example do not declare a sidecar.

---

## Validation Checklist

- [ ] Client-specific logic stays in plugins, not the engine.
- [ ] Protected nodes require the fixed access plugin.
- [ ] Access failures fail closed by hiding protected nodes.
- [ ] Resolver ids referenced by nodes exist in top-level `resolvers`.
- [ ] Plugin class/method references are validated at startup where possible.
- [ ] Secrets stay out of YAML and prompt files.
- [ ] Access and resolver decisions are traced with sensitive values redacted.
