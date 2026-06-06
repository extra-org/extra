# YAML Specification

This document describes the **intended** structure of the declarative input
file (`agent.yml`). It is a design specification — no code parses this yet.
Validation and schema work happens in task `0002`; the compiler in `0003`.

The spec has two conceptual halves:

- **`definitions`** declare *what exists* (providers, MCP servers, tools,
  agents, prompts, context/security requirements).
- **`hierarchy`** declares the *visual nested structure* and routing between
  agents.

Keeping "what exists" separate from "how it is arranged" lets the same agent be
referenced in multiple places and keeps the hierarchy readable.

---

## Conceptual shape

```yaml
version: "1.0"

app:
  name: example-agent-system

runtime:
  entrypoint: root_orchestrator
  mode: monolith_runtime

definitions:
  llm_providers: {}
  mcp_servers: {}
  tools: {}
  agents: {}

hierarchy:
  agent: root_orchestrator
  children:
    - agent: business_orchestrator
      children:
        - agent: invoice_agent

security:
  sidecar:
    enabled: true

observability: {}

deployment: {}
```

---

## Top-level keys

| Key             | Required | Purpose                                                        |
| --------------- | -------- | -------------------------------------------------------------- |
| `version`       | yes      | Spec format version (string, e.g. `"1.0"`).                    |
| `app`           | yes      | Application metadata (`name`, and later description/owner).    |
| `runtime`       | yes      | Runtime settings: `entrypoint` (root agent id), `mode`.        |
| `definitions`   | yes      | All declared entities (see below).                             |
| `hierarchy`     | yes      | The nested agent tree and routing metadata.                    |
| `security`      | no       | Security/sidecar configuration.                                |
| `observability` | no       | Tracing/export configuration.                                  |
| `deployment`    | no       | Deployment-related hints (used by later phases).               |

---

## `definitions`

`definitions` is a set of named maps. Each entry is keyed by a stable **id** that
the hierarchy and other definitions reference.

### `llm_providers`
Declares LLM providers the system can use. A provider entry describes how to
reach a model (provider type, model name, parameters). **Secrets/API keys are
never stored here** — they are referenced indirectly (e.g. an environment
variable name) and resolved at runtime.

```yaml
definitions:
  llm_providers:
    default:
      type: openai          # provider kind
      model: gpt-4o-mini
      api_key_env: OPENAI_API_KEY   # name of an env var, NOT the secret itself
```

### `mcp_servers`
Declares MCP servers the system can connect to (transport, address, allowed
tools). → See [MCP_AND_TOOLS.md](MCP_AND_TOOLS.md).

```yaml
definitions:
  mcp_servers:
    billing_mcp:
      transport: stdio
      command: ["billing-mcp"]
```

### `tools`
Declares tools available to agents, including which MCP server (if any) provides
them, declared input schema, **required permissions**, and an **`input_policy`**
the runtime enforces at the tool layer. → See
[MCP_AND_TOOLS.md](MCP_AND_TOOLS.md).

```yaml
definitions:
  tools:
    invoice_search:
      mcp_server: billing_mcp
      requires_permissions: ["invoice:read"]
      input_policy:
        inject:
          customer_code: "{{ identity.customer_code }}"  # runtime injects trusted value
        block_user_override:
          - customer_code                                 # LLM/user cannot override
```

`input_policy` is the **declarative** tool input policy: `inject` forces trusted
values from resolved context/identity into the call, and `block_user_override`
lists parameters the model/user may **not** set. The runtime (not the prompt)
enforces this at call time, and the sidecar may return additional `tool_policy`
at runtime. → See the security model in
[ARCHITECTURE.md](ARCHITECTURE.md#security--tool-enforcement-model).

### `agents`
Declares **reusable agent definitions**. An agent entry references a provider and
prompt(s), lists the tools it may use, and **declares what it needs**: required
context values and required permissions. The runtime resolves and enforces these.
A definition is declared **once** here and may be referenced **many times** in the
`hierarchy` (each reference becomes a distinct compiled instance — see below).

```yaml
definitions:
  agents:
    invoice_agent:
      provider: default
      prompt: prompts/invoice_agent.md
      tools: ["get_invoice"]
      requires_context: ["tenant_id", "customer_code"]
      requires_permissions: ["invoice:read"]
```

### `prompts`
Prompts may be declared inline or referenced as template files. Either way they
are **templates** with placeholders, rendered per request. → See
[PROMPT_RENDERING.md](PROMPT_RENDERING.md).

### `context_resolvers`
Declares **resolver contracts**: which dynamic context values exist and where
they come from (a generated function, a plugin, the sidecar, the request,
identity, system time, DB/API, or an MCP tool). The YAML declares *what is needed
and its source*; client-specific resolution logic lives **outside** the runtime
(generated resolver package, plugin, or sidecar). The runtime resolves declared
values generically and never contains client business logic.

```yaml
definitions:
  context_resolvers:
    customer_code:
      type: generated_function        # or: sidecar | plugin | request | system | ...
      handler: client_resolvers.customer.resolve_customer_code
      input:
        user_id: "{{ identity.user_id }}"
        tenant_id: "{{ identity.tenant_id }}"
      output:
        name: customer_code
        type: string
        required: true
```

→ See the resolver model in
[ARCHITECTURE.md](ARCHITECTURE.md#resolver-model) and
[ADR 0005](adr/0005-prompt-rendering-and-context-resolution.md).

### `context requirements` & `security requirements`
Agents and tools declare context (`requires_context`) and permissions
(`requires_permissions`). These declarations are what the runtime hands to the
sidecar/resolvers and what the permission layer enforces. → See
[SIDECAR_CONTEXT_AUTH.md](SIDECAR_CONTEXT_AUTH.md).

### `deployment` metadata
Deployment-related metadata (e.g. service name, image hints) is declared at the
top-level `deployment` key and consumed only by later deployment phases. It is
**not** used during validation/compilation of the agent graph.

---

## `hierarchy`

The hierarchy is a tree rooted at the `runtime.entrypoint` agent. Each node
**references an agent definition by id** and may contain `children`, an `as`
instance name, and routing metadata.

```yaml
hierarchy:
  agent: root_orchestrator
  children:
    - agent: business_orchestrator
      routing:
        when: "intent == 'billing'"   # routing metadata (declarative)
      children:
        - agent: invoice_agent
```

### Reusable agents and the `as` keyword

A node under `hierarchy` is a **reference** to an agent *definition*, not the
definition itself. The same definition may be referenced **multiple times** under
different parents. Each occurrence becomes a **distinct compiled instance**.

To keep instances unambiguous, a node may declare `as`, an **instance name**:

```yaml
hierarchy:
  agent: root_orchestrator
  children:
    - agent: invoice_orchestrator
      children:
        - agent: security_review_agent     # definition id
          as: invoice_security_review      # instance name (this occurrence)

    - agent: payment_orchestrator
      children:
        - agent: security_review_agent     # same definition, reused
          as: payment_security_review      # distinct instance
```

Here `security_review_agent` is defined once but appears twice; the two
occurrences compile into two separate instances (`invoice_security_review` and
`payment_security_review`) that share one definition. Because of this, the
hierarchy is a tree/DAG of **references to reusable definitions**, not a tree of
unique agents.
→ See [Agent definitions vs. agent instances](ARCHITECTURE.md#agent-definitions-vs-agent-instances)
and [ADR 0006](adr/0006-reusable-agent-definitions-and-hierarchy-instances.md).

### Rules the validator will enforce (task 0002)

- Exactly one root, and it must match `runtime.entrypoint`.
- Every `hierarchy.agent` id must exist in `definitions.agents`.
- If a definition appears **more than once** in the hierarchy, **each occurrence
  must declare a unique `as`** value.
- `as` (instance) values must be unique within the compiled graph (or at minimum
  unique within a parent scope, if scoped instance ids are adopted).
- If `as` is omitted, the instance id may default to the agent id **only when
  that agent appears exactly once**.
- The compiler must **detect ambiguous repeated references** (the same definition
  used multiple times without distinct `as`) and fail with a clear error.
- **No cycles** in the MVP (a definition must not, directly or transitively,
  contain itself). Cyclic/recursive graphs are out of scope unless explicitly
  added later.
- Routing metadata is **declarative** — it describes *when* to route, it is not
  executable code.

---

## `security`

```yaml
security:
  sidecar:
    enabled: true
    endpoint_env: SIDECAR_URL     # env var name, not a hardcoded URL with secrets
    phases: ["pre_routing", "pre_agent"]
```

When the sidecar is enabled, the runtime calls it per the configured phases. →
See [SIDECAR_CONTEXT_AUTH.md](SIDECAR_CONTEXT_AUTH.md).

---

## Design rules for the spec

- **Declarative only.** YAML describes *what* and *which*, never *how* in code.
  No expressions that are really program logic; routing `when` clauses are
  declarative conditions, not arbitrary code.
- **References by id.** Cross-links use stable ids resolved at compile time.
- **No secrets.** Only references to secrets (env var names), never values.
- **Validated before use.** The compiler trusts only validated specs.

→ See [ADR 0002](adr/0002-yaml-is-compiled-not-executed-directly.md).
