# MCP & Tools

This document defines how agents use tools and MCP servers, and how the
**tool permission layer** enforces safety. It is a design specification; tools
and MCP work is task `0007`.

---

## Concepts

- **MCP server** — an external process/service (Model Context Protocol) that
  exposes tools. Declared in `definitions.mcp_servers`.
- **Tool** — a callable capability available to agents. A tool may be provided
  by an MCP server or be built in. Declared in `definitions.tools`.
- **Tool binding** — an agent lists the tool ids it may use. The runtime binds
  these to actual callables at startup.

---

## Declaring tools

```yaml
definitions:
  mcp_servers:
    billing_mcp:
      transport: stdio
      command: ["billing-mcp"]

  tools:
    get_invoice:
      mcp_server: billing_mcp
      description: "Fetch an invoice by id."
      input_schema:
        type: object
        properties:
          invoice_id: { type: string }
        required: ["invoice_id"]
      requires_permissions: ["invoice:read"]
      input_policy:
        inject:
          customer_code: "{{ identity.customer_code }}"  # forced by the runtime, not the model
        block_user_override:
          - customer_code
```

> **Terminology.** `input_policy` is the **declarative** tool input policy in
> YAML: `inject` (force trusted values) and `block_user_override` (parameters the
> model/user may not set). At runtime the sidecar may additionally return a
> `tool_policy` (allow/deny + injects) per
> [SIDECAR_CONTEXT_AUTH.md](SIDECAR_CONTEXT_AUTH.md). Both are enforced at the
> tool layer; the prompt is never the boundary.

Agents opt in to tools:

```yaml
definitions:
  agents:
    invoice_agent:
      tools: ["get_invoice"]
      requires_permissions: ["invoice:read"]
```

---

## The tool permission layer (binding)

Every tool call passes through enforcement **at call time**, using the resolved
`ExecutionContext` (identity, permissions, tool policy from the sidecar):

1. **Permission check.** The caller must hold every permission in the tool's
   `requires_permissions` and must not be denied by the sidecar's `tool_policy`.
   Otherwise the call is **blocked** and traced.
2. **Injected parameters.** `input_policy.inject` (and sidecar
   `tool_policy.inject`) values are **forced** into the call, and
   `input_policy.block_user_override` parameters cannot be set by the model/user.
   The model's proposed arguments **cannot override** these. This is how tenant
   isolation is guaranteed regardless of what the model "decides".
3. **Input validation.** Arguments are validated against the tool's
   `input_schema` before invocation.
4. **Trace.** The decision, final arguments (redacted as needed), and result
   status are recorded in the trace.

> **Prompt text alone is not a security boundary.** A prompt asking the model to
> "only access tenant 42" is not enforcement. The permission layer and injected
> parameters are the enforcement. → See
> [SIDECAR_CONTEXT_AUTH.md](SIDECAR_CONTEXT_AUTH.md).

---

## MCP connection lifecycle

- MCP connections/clients are set up **at startup** (or lazily but shared), held
  by the long-lived runtime, and reused across requests.
- They are **not** recreated per request. → See
  [RUNTIME_LIFECYCLE.md](RUNTIME_LIFECYCLE.md).
- Per-request data (identity, injected values) flows via the
  `ExecutionContext`, not via shared connection state.

---

## Secrets

- MCP server credentials and tool API keys are **never** stored in YAML. Use
  references (env var names) resolved at runtime.
- Secrets are redacted in traces.

---

## Validation checklist (for tool/MCP changes)

- [ ] Tool calls cannot proceed without required permissions.
- [ ] Injected/policy parameters cannot be overridden by model output.
- [ ] Tool inputs are validated against the declared schema.
- [ ] MCP connections are created once and shared, not per request.
- [ ] No secrets in YAML; secrets redacted in traces.
- [ ] Blocked calls are traced with a reason.
- [ ] `make check` passes.
