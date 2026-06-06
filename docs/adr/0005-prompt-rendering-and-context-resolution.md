# ADR 0005: Prompt templates are rendered per request using resolved context

## Status

Accepted

## Context

Prompt values such as `customer_code`, `current_date`, `tenant_id`, permissions,
and customer profile are dynamic and can change per request.

The platform must allow clients to define required prompt variables in YAML
without changing the generated runtime code.

Some values may come from request data, identity claims, the sidecar, DB/API
lookups, MCP tools, generated resolver functions, or plugins.

## Decision

Prompt files are treated as templates.

Raw prompt templates may be loaded and cached.

Rendered prompts are created per request.

The runtime resolves required context values through a `ContextResolver` system
before rendering the selected agent's prompts.

Client-specific context logic must live in sidecar, generated resolver package,
or plugin boundaries.

YAML declares what values are needed and where they come from, but YAML must not
contain executable business logic.

## Consequences

- `RuntimeEngine` can cache prompt templates safely.
- `ExecutionContext` owns resolved values for one request.
- Prompt rendering is deterministic for each request.
- Missing required variables should fail clearly.
- Client-specific logic stays outside the core runtime.
- Prompt text is not a security boundary.
- Tool permissions and injected parameters must be enforced by runtime/tool
  policy.

## Alternatives Considered

1. **Render prompts at startup.** Rejected because dynamic values change per
   request.
2. **Let clients modify generated runtime code.** Rejected because the runtime
   must stay generic and maintainable.
3. **Put business logic directly inside YAML.** Rejected because YAML should stay
   declarative and not become a programming language.
4. **Let prompts enforce security by instruction only.** Rejected because prompt
   instructions are not enforceable security boundaries.

## Related

- [ADR 0001 — RuntimeEngine created once](0001-runtime-engine-created-once.md)
- [ADR 0002 — YAML is compiled, not executed directly](0002-yaml-is-compiled-not-executed-directly.md)
- [ADR 0003 — Client-specific logic lives in the sidecar](0003-client-specific-logic-lives-in-sidecar.md)
- [ADR 0004 — Prompts are templates rendered per request](0004-prompts-are-templates-rendered-per-request.md)
- Docs: [PROMPT_RENDERING.md](../PROMPT_RENDERING.md),
  [SIDECAR_CONTEXT_AUTH.md](../SIDECAR_CONTEXT_AUTH.md),
  [ARCHITECTURE.md](../ARCHITECTURE.md)

> This ADR records a **decision**. The `ContextResolver` system, generated
> resolver packages, and tool-policy enforcement described here are **not
> implemented yet**; they are built by tasks `0005`–`0007`.
