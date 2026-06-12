# Plugin Context & Access

This document defines the current context and access model. The filename is
kept for link stability, but the current MVP design is plugin-based rather than
sidecar-first.

Customer-specific authentication, authorization, database access, REST clients,
and business context live in customer Python plugins. The engine remains generic:
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
methods receive `ctx`; customer code decides how to interpret tokens, user ids,
tenant ids, roles, subscriptions, and business data.

---

## Resolver Plugins

Resolvers fill prompt variables before a node runs. They are deterministic from
the engine's point of view and are not exposed to the LLM.

```yaml
resolvers:
  current_date:
    scope: shared
    return_type: str
```

```toml
[resolvers]
base_class = "plugins.resolvers.base.BaseResolver"

[resolvers.agents.super_agent]
class = "plugins.resolvers.super_agent.SuperAgentResolver"
```

```python
from agentplatform.runtime import ExecutionContext


class BaseResolver:
    def __init__(self, rest_client: object | None = None) -> None:
        self.rest_client = rest_client

    def current_date(self, ctx: ExecutionContext) -> str:
        return "2026-06-06"


class SuperAgentResolver(BaseResolver):
    pass
```

A node opts into resolver values:

```yaml
agents:
  super_agent:
    description: "Handle supermarket orders."
    prompts:
      system: "prompts/super/system.md"
    resolvers: [current_date]
```

Missing resolver declarations, missing plugin methods, resolver errors, or
missing variables in strict prompt rendering should produce clear errors.

---

## Access Plugin

Authorization is opt-in per node. A node marked `protected: true` is hidden from
the router unless the fixed access plugin allows it.

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
- `protected: true` without `plugins/access.py` is a configuration error at
  startup.
- `plugins/access.py` with no protected nodes is harmless and may be ignored.

---

## Why Not YAML Roles?

The engine should not invent a role model. Customers already have auth and RBAC
systems. The access plugin receives the request context and node id, then asks
the customer's existing system whether access is allowed.

This keeps adoption fast and avoids baking one company's permission model into
the generic runtime.

---

## Future Sidecar Option

A separate sidecar service may still be useful later for stronger isolation or
non-Python customer logic. If reintroduced, it should be treated as another
extension boundary with an explicit ADR and updated schema. The current schema
and example do not declare a sidecar.

---

## Validation Checklist

- [ ] Customer-specific logic stays in plugins, not the engine.
- [ ] Protected nodes require the fixed access plugin.
- [ ] Access failures fail closed by hiding protected nodes.
- [ ] Resolver ids referenced by nodes exist in top-level `resolvers`.
- [ ] Plugin class/method references are validated at startup where possible.
- [ ] Secrets stay out of YAML and prompt files.
- [ ] Access and resolver decisions are traced with sensitive values redacted.
