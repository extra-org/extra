# Sidecar: Context & Auth

This document defines the **client-owned sidecar** model. It is a design
contract; no client and no runtime caller are implemented yet (sidecar work is
task `0006`).

→ See [ADR 0003](adr/0003-client-specific-logic-lives-in-sidecar.md) and
[ADR 0005](adr/0005-prompt-rendering-and-context-resolution.md).

The sidecar is the **enterprise form of the resolver model**: an external,
client-owned service that resolves identity, context, permissions, and tool
policy through a standard contract, instead of (or alongside) in-process resolver
packages/plugins. → See the resolver model in
[ARCHITECTURE.md](ARCHITECTURE.md#resolver-model).

---

## The idea

The generated runtime contains **no** client-specific authentication,
authorization, or business-data lookup code. Different companies authenticate
differently, model tenants differently, and store data differently. Baking any
one company's model into the runtime would make it un-reusable.

Instead, the client implements a **Context/Auth Sidecar**: a small service that
speaks a standard contract. The runtime calls it at well-defined phases to
resolve who the caller is and what context/permissions apply.

## Division of responsibility

### The sidecar is responsible for (client-specific):

- authentication
- authorization
- identity resolution
- `tenant_id` / `user_id` resolution
- `customer_code` resolution
- roles and permissions
- customer profile
- third-party API calls
- database lookups
- business-specific / dynamic context
- tool input policies

### The runtime is responsible for (generic):

- calling the sidecar using the standard contract
- creating or **enriching** the `ExecutionContext` from the response
- checking authorization (`allowed`)
- using resolved context for prompt rendering
- enforcing permissions
- enforcing tool input policies
- tracing the decision

The runtime **never** implements the client's auth or business rules — it only
*calls*, *maps*, *enforces*, and *traces*.

---

## When the runtime calls the sidecar

Two optional phases, both using the same contract (see `phase`):

- **`pre_routing`** — before the runtime routes the request through the
  hierarchy. Use when routing depends on identity/tenant/role.
- **`pre_agent`** — after an agent is selected, before it executes. Use for
  agent-specific permissions or data lookups.

Either phase may be disabled per the spec's `security.sidecar.phases`.

---

## Contract: `POST /resolve-context`

A single endpoint handles both phases. The runtime is the client; the sidecar is
the server.

### Request

| Field                 | Type            | Description                                              |
| --------------------- | --------------- | -------------------------------------------------------- |
| `phase`               | string          | `"pre_routing"` or `"pre_agent"`.                        |
| `request_id`          | string          | Correlates with the trace.                               |
| `agent_id`            | string \| null  | The selected agent (null during `pre_routing`).          |
| `required_context`    | string[]        | Context keys the agent/tool declared it needs.           |
| `required_permissions`| string[]        | Permissions the agent/tool declared it needs.            |
| `headers`             | object          | Relevant request headers (e.g. auth token).              |
| `body_metadata`       | object          | Non-sensitive request metadata to aid resolution.        |

```json
{
  "phase": "pre_agent",
  "request_id": "req_01H...",
  "agent_id": "invoice_agent",
  "required_context": ["tenant_id", "customer_code"],
  "required_permissions": ["invoice:read"],
  "headers": { "authorization": "Bearer <redacted-in-trace>" },
  "body_metadata": { "intent": "billing" }
}
```

### Response

| Field         | Type            | Description                                                       |
| ------------- | --------------- | ----------------------------------------------------------------- |
| `allowed`     | boolean         | Whether the request may proceed.                                  |
| `identity`    | object \| null  | Resolved identity (e.g. `user_id`, `tenant_id`, `customer_code`). |
| `permissions` | string[]        | Granted permissions.                                              |
| `context`     | object          | Resolved dynamic context values (keyed by context name).          |
| `tool_policy` | object          | Per-tool input policies (allow/deny, value constraints, injects). |
| `reason`      | string \| null  | Human-readable explanation, especially when `allowed` is false.   |

```json
{
  "allowed": true,
  "identity": { "user_id": "u_123", "tenant_id": "t_42", "customer_code": "ACME" },
  "permissions": ["invoice:read"],
  "context": { "tenant_id": "t_42", "customer_code": "ACME", "locale": "en-GB" },
  "tool_policy": {
    "get_invoice": { "allow": true, "inject": { "tenant_id": "t_42" } }
  },
  "reason": null
}
```

---

## How the runtime uses the response

1. If `allowed` is `false`, the runtime **stops** and returns a denial
   (traced with `reason`). It does not execute the agent.
2. `identity`, `permissions`, and `context` are mapped onto the
   `ExecutionContext`.
3. `context` values feed **prompt rendering**.
4. `permissions` are checked by the **tool permission layer** before any tool
   call.
5. `tool_policy` is applied at tool-call time: denied tools are blocked, and
   `inject` values are forced into tool parameters (the model cannot override
   them).
6. The whole decision is **traced** (with secrets redacted).

---

## Security notes

- **Prompt text is not a security boundary.** Even if a prompt "says" the model
  may only read tenant 42's data, enforcement must happen at the tool/data layer
  using `permissions` and `tool_policy`. → See
  [PROMPT_RENDERING.md](PROMPT_RENDERING.md).
- **Secrets stay out of YAML and prompts.** Auth tokens live in headers/env and
  are redacted in traces.
- **Fail closed.** If the sidecar is enabled but unreachable or returns an
  error, the runtime denies the request rather than proceeding without context.

---

## Validation checklist (for sidecar-related changes)

- [ ] No client-specific auth/business logic added to the runtime.
- [ ] Request/response shapes match this contract (or this doc is updated via an
      ADR if the contract must change).
- [ ] `allowed: false` blocks execution and is traced.
- [ ] `tool_policy.inject` values cannot be overridden by model output.
- [ ] Secrets are redacted in traces.
- [ ] Fail-closed behavior is tested.
- [ ] `make check` passes.
