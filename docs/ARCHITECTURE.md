# Architecture

This document describes the high-level design of the Declarative Agent Platform.
It is the conceptual blueprint; implementation is built task-by-task (see
`tasks/`). Nothing here is working software yet.

> **Architecture decision vs. future implementation.** This document records
> *decisions* about how the system is meant to work. The behaviors it describes
> (runtime, resolvers, sidecar, tool policy) are **not implemented yet**; each is
> built later by a numbered task. Decisions that are binding are captured as
> ADRs in [`adr/`](adr/).

---

## Product vision

A single declarative `agent.yml` fully describes an AI-agent system. A developer
declares *what exists* (LLM providers, MCP servers, tools, agents, prompts,
resolver contracts, context/security requirements) and *how it is structured*
(a nested agent hierarchy). The platform then:

- **validates** the YAML,
- **compiles** it into a typed, immutable agent graph,
- builds a **long-lived `RuntimeEngine`** once at startup,
- creates a fresh **`ExecutionContext` per request**,
- **resolves dynamic context** for the selected agent,
- **renders prompt templates per request** from that context,
- **executes** the selected agent (recursively through the hierarchy),
- **enforces tool permissions and injected parameters outside the prompt**, and
- returns a **response with a trace**.

Client-specific authentication, authorization, and business logic stay **outside**
the generated runtime — in a client-owned **sidecar** or in **generated resolver
packages / plugins**. The core runtime remains generic and reusable.

---

## Design goals

- A YAML specification fully describes an agent system.
- The specification is **validated** and **compiled** before it ever runs.
- A **single long-lived runtime** executes many requests against an immutable
  compiled graph.
- **Client-specific concerns** (auth, business context) are pushed to a sidecar.
- Every request is **traceable**.

---

## Layers

The system is organized as a one-directional pipeline of layers. Each layer has
a single responsibility and a typed boundary with its neighbors.

### 1. Spec layer
Loads `agent.yml` from disk into in-memory data, then into raw schema models.
It does **not** execute anything. Output: parsed spec models.
→ See [YAML_SPEC.md](YAML_SPEC.md).

### 2. Validation layer
Validates the spec: schema correctness, required fields, referential integrity
(every referenced agent/tool/provider exists), hierarchy well-formedness (no
cycles, single root), and security/context declarations. Validation **must**
happen before compilation. Output: a validated spec or a structured error set.

### 3. Compiler layer
Transforms the validated spec into typed, immutable internal models. This is
where declarative YAML becomes a real object graph. The compiler resolves
references, expands the hierarchy, and links prompts/tools/providers to agents.
Output: a `CompiledAgentGraph`. The runtime never sees raw YAML.
→ See [ADR 0002](adr/0002-yaml-is-compiled-not-executed-directly.md).

### 4. Agent graph layer
The `CompiledAgentGraph` is an immutable, validated object graph: agents,
their parent/child routing relationships, attached prompts, tool bindings,
provider bindings, and declared context/permission requirements. Built once;
shared read-only by all requests.

### 5. Runtime layer
The `RuntimeEngine` is constructed **once at application startup** from the
compiled graph. It is long-lived and stateless with respect to individual
requests. For each request it creates a fresh `ExecutionContext`, routes through
the hierarchy, and executes the selected agent(s) recursively.
→ See [RUNTIME_LIFECYCLE.md](RUNTIME_LIFECYCLE.md) and
[ADR 0001](adr/0001-runtime-engine-created-once.md).

### 6. Prompt rendering layer
Loads prompt **templates** (cacheable) and renders them **per request** using
values from the `ExecutionContext`. Missing required variables fail loudly.
Rendered prompts are never cached globally.
→ See [PROMPT_RENDERING.md](PROMPT_RENDERING.md) and
[ADR 0004](adr/0004-prompts-are-templates-rendered-per-request.md).

### 7. Context resolver layer
Assembles the values used for prompt rendering and tool calls. Context can come
from the request, identity, the sidecar, system time, memory, tools, databases,
APIs, or plugins. Agents **declare** required context; the resolver gathers it.

### 8. Sidecar auth/context layer
Calls the client-owned sidecar over a standard contract to resolve identity,
tenant, permissions, dynamic context, and tool input policies. The runtime maps
the response into the `ExecutionContext`. No client-specific logic lives in the
runtime itself.
→ See [SIDECAR_CONTEXT_AUTH.md](SIDECAR_CONTEXT_AUTH.md) and
[ADR 0003](adr/0003-client-specific-logic-lives-in-sidecar.md).

### 9. MCP/tool layer
Connects agents to MCP servers and tools declared in the spec. Manages tool
discovery, parameter binding, and invocation.
→ See [MCP_AND_TOOLS.md](MCP_AND_TOOLS.md).

### 10. Tool permission layer
Enforces, at call time, that the resolved identity/permissions allow a tool
call, that injected parameters are applied, and that tool input policies from
the sidecar are honored. **Prompt text is not a security boundary** — this layer
is. Denied calls are blocked and traced.

### 11. Observability layer
Produces a structured trace for every request: routing decisions, sidecar
calls, resolved context (redacted), prompt rendering, tool calls, and the final
response. Used for debugging and auditing.

### 12. API layer
Exposes the runtime over HTTP. Constructs the `RuntimeEngine` once at startup
and creates an `ExecutionContext` per request. Never builds a runtime per
request.

### 13. CLI layer
Command-line entry points for validating, compiling, inspecting, and locally
running an `agent.yml`.

### 14. Deployment layer
Packaging and deployment concerns (e.g. Docker). Out of scope until late tasks.

---

## Request flow

```
Incoming request
  → Security/Context Gate          (reject obviously invalid/unauthenticated calls)
  → optional pre-routing sidecar call   (resolve identity/context needed for routing)
  → RuntimeEngine
  → route through hierarchy        (choose the agent path from the root entrypoint)
  → optional pre-agent sidecar call     (resolve context/permissions for the chosen agent)
  → resolve context                (assemble values from all declared sources)
  → render prompts                 (templates → text, per request)
  → execute selected agent         (recursively, including children as needed)
  → enforce tool permissions       (block disallowed tool calls; apply policies)
  → return response and trace
```

### Why two sidecar phases?

Some context is needed **before** the runtime can route (e.g. tenant/role-based
routing). Other context is only needed **once an agent is chosen** (e.g.
agent-specific permissions or data lookups). Both phases use the same contract;
either may be skipped if not required. See `phase` in
[SIDECAR_CONTEXT_AUTH.md](SIDECAR_CONTEXT_AUTH.md).

---

## Component lifetimes (summary)

| Component            | Created            | Lifetime        | Holds request state? |
| -------------------- | ------------------ | --------------- | -------------------- |
| Spec / validation    | build/load time    | transient       | no                   |
| `CompiledAgentGraph` | startup (once)     | application     | **no (immutable)**   |
| `RuntimeEngine`      | startup (once)     | application     | **no**               |
| Prompt templates     | first use / startup| cached          | no                   |
| `ExecutionContext`   | per request        | request         | **yes**              |
| Trace                | per request        | request         | yes                  |

This table is binding. Violating it (e.g. putting request state on the engine,
or rebuilding the graph per request) is an architecture rule violation.

---

## RuntimeEngine vs. ExecutionContext

The single most important runtime decision: **`RuntimeEngine` is created once at
startup; `ExecutionContext` is created per request.** Their ownership boundaries
are strict and must not blur.
→ See [RUNTIME_LIFECYCLE.md](RUNTIME_LIFECYCLE.md) and
[ADR 0001](adr/0001-runtime-engine-created-once.md).

**`RuntimeEngine` owns long-lived infrastructure** (shared, read-only, no request
state):

- the `CompiledAgentGraph`
- the prompt template loader (and raw-template cache)
- the resolver registry
- the sidecar client
- the tool registry
- the MCP manager
- the LLM provider registry
- observability/tracing infrastructure

**`ExecutionContext` owns request-specific state** (created fresh per request,
discarded at the end):

- `request_id`
- `tenant_id`, `user_id`
- `identity`
- `permissions`
- resolved context values
- the selected agent path
- prompt render values
- trace events
- temporary tool results

**Never store request-specific state on `RuntimeEngine`.** Doing so leaks data
between concurrent requests and breaks the concurrency model.

---

## YAML: definitions vs. hierarchy

The spec is declarative and split into two conceptual halves. It must not contain
executable business logic.
→ See [YAML_SPEC.md](YAML_SPEC.md) and
[ADR 0002](adr/0002-yaml-is-compiled-not-executed-directly.md).

**1. `definitions` — what exists.** LLM providers, MCP servers, tools, agents,
prompt paths, resolver contracts, context requirements, and security
requirements. Each entry has a stable id referenced elsewhere.

**2. `hierarchy` — how the system is structured.** A visual, indented nesting of
agents (parent → children) rooted at `runtime.entrypoint`, plus routing metadata.

```yaml
hierarchy:
  agent: root_orchestrator
  children:
    - agent: business_orchestrator
      children:
        - agent: invoice_orchestrator
          children:
            - agent: invoice_reader_agent
            - agent: invoice_writer_agent
```

Definitions declare *what exists*; hierarchy declares *how it is arranged*. The
compiler links the two into the `CompiledAgentGraph`.

---

## Dynamic prompt rendering

Prompt files are **templates**. Raw templates may be loaded and cached at
startup; **rendered prompts are produced per request** from values resolved for
that request (e.g. `customer_code`, `current_date`, `tenant_id`, permissions,
customer profile). Missing required variables fail clearly; rendering is **strict
by default**. Rendered prompts are never cached globally.
→ See [PROMPT_RENDERING.md](PROMPT_RENDERING.md) and
[ADR 0005](adr/0005-prompt-rendering-and-context-resolution.md).

---

## Resolver model

Many context values are dynamic and client-specific. They may come from the
request body, request headers, auth/JWT claims, identity context, system time,
memory, a DB lookup, a third-party API, an MCP tool, the client-owned sidecar, a
**generated resolver package**, or a plugin.

**Clients must not modify core runtime code to resolve these values.** Instead,
the client *declares* the required context and its source in YAML, and the
runtime resolves it through a generic **`ContextResolver`** mechanism registered
in the resolver registry.

```yaml
# illustrative — not yet implemented
context_resolvers:
  customer_code:
    type: generated_function
    handler: client_resolvers.customer.resolve_customer_code
    input:
      user_id: "{{ identity.user_id }}"
      tenant_id: "{{ identity.tenant_id }}"
    output:
      name: customer_code
      type: string
      required: true

agents:
  invoice_reader_agent:
    context:
      customer_code:
        use: customer_code
```

The runtime, per request, determines the selected agent's required context,
invokes the declared resolvers to produce values, and only then renders prompts.
The resolver registry (a `RuntimeEngine` collaborator) holds the available
resolvers; resolved values live on the `ExecutionContext`.

### Generated resolver project (concept)

To keep the runtime generic, the platform may **generate resolver function stubs**
for the client from the YAML resolver contracts. The client implements **only the
business logic** inside that generated project (e.g. how to look up a
`customer_code` from a `user_id`/`tenant_id`). The core runtime imports/calls
those handlers through the generic resolver contract and never contains
client-specific logic itself. This is a planned capability, not yet built.

---

## Sidecar model

For enterprise cases, client-specific authentication, authorization, and context
resolution live **outside** the runtime in a client-owned **sidecar** that speaks
a standard contract. The runtime calls the sidecar (optionally before routing and
before agent execution), maps the response into the `ExecutionContext`, checks
authorization, feeds prompt rendering, enforces tool policies, and traces the
decision. The sidecar is, in effect, an external resolver of identity,
permissions, context, and tool policy.
→ See [SIDECAR_CONTEXT_AUTH.md](SIDECAR_CONTEXT_AUTH.md) and
[ADR 0003](adr/0003-client-specific-logic-lives-in-sidecar.md).

---

## Security / tool enforcement model

**Prompt text is not a security boundary.** A prompt instruction like
*"Only answer for customer {{ customer_code }}"* is not enforceable — the model or
user could ignore it. Enforcement happens at the **tool/data layer** via runtime
policy.

```yaml
# illustrative — not yet implemented
tools:
  invoice_search:
    input_policy:
      inject:
        customer_code: "{{ identity.customer_code }}"
      block_user_override:
        - customer_code
```

This means: the LLM cannot choose `customer_code`; the user cannot override it;
the runtime **injects** the trusted value from resolved identity; and the tool
call is constrained by runtime policy. Injected/blocked parameters are enforced
at call time regardless of prompt or model output, and denials are traced.
→ See [MCP_AND_TOOLS.md](MCP_AND_TOOLS.md) and the tool permission layer above.

---

## First implementation step

Architecture is documented; **no runtime exists yet**. The first implementation
step is the repository/package foundation, then the spec & validation layer:

1. `tasks/0001-repository-foundation.md` — create the `src/agentplatform/`
   package skeleton and test layout.
2. `tasks/0002-yaml-schema-and-validation.md` — typed schema models + validation
   (validate before compile).

Subsequent tasks build the compiler (`0003`), runtime engine (`0004`), prompt
rendering (`0005`), sidecar/resolvers (`0006`), and tools/permissions (`0007`).
Work proceeds task-by-task; do not implement ahead of the current task's scope.
