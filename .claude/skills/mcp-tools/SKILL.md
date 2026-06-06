---
name: mcp-tools
description: Use when working on tools or MCP integration. The runtime owns connections; tools have allowlists; protected inputs cannot be overridden.
---

# Skill: MCP & Tools

## Purpose

Integrate tools/MCP servers and enforce permissions and injected parameters at
the tool layer. Primary task: `0007`.

## When to Use

- Working on the tool registry, MCP connections, tool invocation, or
  permission/policy enforcement.

## Files to Read First

- `skills/mcp-tools-skill.md` (root playbook).
- `docs/MCP_AND_TOOLS.md`, `docs/SIDECAR_CONTEXT_AUTH.md` (permissions/policy).

## Rules

- Agents do not open MCP connections directly; the **runtime owns MCP connection
  management** (created once, shared, not per request).
- Tools have allowlists (agents opt into specific tool ids).
- Tool inputs can be injected by policy (`injected_params` /
  `tool_policy.inject`); the user/LLM **cannot override** protected inputs.
- Enforce `requires_permissions` at call time; block + trace denials.
- Validate tool inputs against the declared schema. Prompt text is not security.
- No secrets in YAML; redact secrets in traces.

## Process

1. Build the tool registry at startup from the compiled graph.
2. Hold MCP connections on the long-lived runtime; pass per-request data via
   `ExecutionContext`.
3. At call time: permission check → force injected/policy params → schema-
   validate → invoke → trace.
4. Test allow/deny/injection-override/schema-violation; `make check`.

## Checklist Before Finishing

- [ ] Runtime owns MCP connections (once, shared); agents don't open them.
- [ ] Allowlists enforced; protected inputs non-overridable.
- [ ] Permissions checked + denials traced; inputs schema-validated.
- [ ] No secrets in YAML; redacted in traces; `make check` passes.

## Common Mistakes to Avoid

- Letting the model override injected/tenant values; per-request MCP connections.
- Skipping input validation; relying on the prompt to restrict tools.

## Expected Final Report

Confirm runtime-owned connections, allowlists, non-overridable injected inputs,
permission enforcement + tracing, schema validation, and the `make check`
result.
