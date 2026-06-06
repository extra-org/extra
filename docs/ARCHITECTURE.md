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

This is **agent architecture as code**: the YAML is the declarative source of
truth, and the runtime is a generic execution engine — not a framework for
hand-writing agents.

---

## Execution phases: build vs. runtime vs. client extension

The most important structural rule of the project is the separation of three
phases. **Do not collapse these layers.**
→ Decision recorded in
[ADR 0007](adr/0007-build-phase-separate-from-runtime-phase.md).

### 1. Build / generation / compilation phase

Happens **before** any real user request is served. Responsible for:

- loading `agent.yml`,
- validating the YAML schema, references, prompt paths, tools, MCP servers, the
  hierarchy, reusable agent instances, resolver references, and "no hardcoded
  secrets",
- detecting invalid cycles,
- compiling the YAML into a `CompiledAgentGraph`,
- (later) optionally generating resolver stubs for client business logic,
- (later) optionally generating deployment artifacts.

It answers: *"Is this agent system valid? What runtime structure should be created
from it?"* **It must not execute user requests.**

### 2. Runtime / execution phase

Happens when the service is running and receives requests. Responsible for:

- receiving a `/chat` or CLI request,
- creating a fresh `ExecutionContext` per request,
- calling the sidecar/resolvers if configured and enriching context (identity,
  permissions, tenant, `customer_code`, …),
- routing through the compiled graph and selecting the correct **agent
  instance**,
- resolving dynamic prompt variables and rendering prompts **for this request**,
- executing the selected agent,
- enforcing tool permissions and injecting protected tool parameters,
- calling MCP/tools if needed,
- returning a response and trace.

It answers: *"A request arrived. Which agent should handle it, with what context
and permissions?"*

### 3. Client extension layer

Client-specific business logic is **never hardcoded into the generated runtime**.
It lives in extension boundaries: generated resolver functions, a client-owned
sidecar service, plugin packages, or custom resolver implementations. Examples:
authentication, authorization, `customer_code`/tenant/permission lookups, DB
queries, third-party API calls, business-specific context, and tool input
policies. **The core runtime stays generic.**
→ See [ADR 0003](adr/0003-client-specific-logic-lives-in-sidecar.md).

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
references, **expands each `hierarchy` reference into a distinct
`CompiledAgentInstance`** that points to a shared `AgentDefinition`, and links
prompts/tools/providers to definitions. Output: a `CompiledAgentGraph`. The
runtime never sees raw YAML.
→ See [ADR 0002](adr/0002-yaml-is-compiled-not-executed-directly.md) and
[ADR 0006](adr/0006-reusable-agent-definitions-and-hierarchy-instances.md).

### 4. Agent graph layer
The `CompiledAgentGraph` is an immutable, validated object graph of
**`CompiledAgentInstance` nodes** — each a specific occurrence of a reusable
`AgentDefinition` — with their parent/child routing relationships, attached
prompts, tool bindings, provider bindings, and declared context/permission
requirements. The same definition may back several instances. Built once; shared
read-only by all requests. → See
[Agent definitions vs. agent instances](#agent-definitions-vs-agent-instances).

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
- the prompt renderer
- the resolver registry
- the sidecar client
- the tool registry
- the MCP manager
- the LLM provider registry
- observability/tracing infrastructure
- runtime configuration

It must **not** store: current `user_id`, `tenant_id`, `customer_code`,
permissions, request message, rendered prompt, trace path, or tool results.

**`ExecutionContext` owns request-specific state** (created fresh per request,
discarded at the end):

- `request_id`
- `message`
- `tenant_id`, `user_id`, `session_id`
- `identity`
- `roles`, `permissions`
- `customer_code`
- resolved context values
- the selected agent path
- rendered prompt values
- temporary tool results
- trace events
- errors

**Never store request-specific state on `RuntimeEngine`.** Doing so leaks data
between concurrent requests and breaks the concurrency model.

> **Simple rule:** `RuntimeEngine` = *how the system works*;
> `ExecutionContext` = *what is happening in this specific request*.

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

## Agent definitions vs. agent instances

The YAML carries two distinct concepts, and the compiler keeps them separate:

- **Agent Definition** — a reusable declaration under `definitions.agents`. It
  describes *what an agent is* (type, prompts, tools, provider, declared
  context/permission requirements). Declared **once**.
- **Agent Instance / graph node** — a specific **occurrence** of a definition
  inside the `hierarchy`. It describes *where the agent sits* in the tree (its
  parent and path).

**The same definition may appear multiple times** under different parents. Each
occurrence is a separate compiled instance that points back to the one shared
definition. So the hierarchy is a tree/DAG of **references to reusable
definitions**, not a tree of unique agents.

### Why `as` exists

When a definition is referenced more than once, the instances need stable,
unambiguous identities for routing, tracing, and path-specific context. The `as`
keyword names an occurrence:

```yaml
hierarchy:
  agent: root_orchestrator
  children:
    - agent: invoice_orchestrator
      children:
        - agent: security_review_agent
          as: invoice_security_review
    - agent: payment_orchestrator
      children:
        - agent: security_review_agent
          as: payment_security_review
```

`security_review_agent` is defined once but appears twice;
`invoice_security_review` and `payment_security_review` are **distinct graph node
instances** that share the same definition.

### Internal model (compiler)

The compiler distinguishes the reusable definition from each compiled instance:

```text
AgentDefinition
  id: security_review_agent
  type: specialist
  prompts: ...
  tools: ...

CompiledAgentInstance
  instance_id: invoice_security_review
  agent_id: security_review_agent
  parent_instance_id: invoice_orchestrator
  path: root_orchestrator.invoice_orchestrator.invoice_security_review

CompiledAgentInstance
  instance_id: payment_security_review
  agent_id: security_review_agent
  parent_instance_id: payment_orchestrator
  path: root_orchestrator.payment_orchestrator.payment_security_review
```

Each instance carries its own `instance_id`, the `agent_id` of its definition,
its `parent_instance_id`, and a fully-qualified `path`. The definition holds the
shared, reusable configuration.

### Runtime rule

The **runtime executes `CompiledAgentInstance` nodes, not definitions.** An
instance points to its `AgentDefinition` for configuration. Executing instances
(rather than definitions) enables:

- reusable agents,
- clear tracing (each occurrence is distinct),
- path-specific routing,
- path-specific context,
- optional future instance-level overrides.

→ See [RUNTIME_LIFECYCLE.md](RUNTIME_LIFECYCLE.md).

### Tracing reused agents

Trace events use the **`instance_id`** as the primary identity (so reused agents
are distinguishable in a trace) and **also include the original `agent_id`** (so
the trace shows which definition was executed). Including the `path` makes the
position in the hierarchy explicit. → See the observability layer above.

### Validation rules (summary)

The validator/compiler enforce (see [YAML_SPEC.md](YAML_SPEC.md) for detail):

1. Every `hierarchy.agent` references an existing `definitions.agents` entry.
2. A definition used more than once must give each occurrence a unique `as`.
3. `as` (instance) ids are unique within the compiled graph (or at least within a
   parent scope).
4. If `as` is omitted, the instance id defaults to the agent id **only** when the
   agent appears exactly once.
5. Ambiguous repeated references (reused definition without distinct `as`) are
   rejected with a clear error.
6. No cycles in the MVP.
7. Traces use `instance_id` and also include `agent_id`.
8. Runtime executes instances, not only definitions.

→ Decision recorded in
[ADR 0006](adr/0006-reusable-agent-definitions-and-hierarchy-instances.md).

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

To keep the runtime generic, the platform may **generate a separate Python project
with resolver signatures** from the YAML resolver contracts. The client implements
**only the business logic** inside that generated project (e.g. how to look up a
`customer_code` from a `user_id`/`tenant_id`). The core runtime imports/calls
those handlers through the generic resolver contract and never contains
client-specific logic itself.

```python
# generated stub (illustrative — not yet implemented)
async def resolve_customer_code(
    context: ResolverContext,
    user_id: str,
    tenant_id: str,
) -> ResolverResult[str]:
    raise NotImplementedError("Client must implement this resolver")
```

The runtime supplies a `ResolverContext`, calls the handler, and maps the returned
`ResolverResult` into the `ExecutionContext`. This is a planned capability, not yet
built.

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

## Open-source, self-hosted developer experience

The platform is self-hosted open source. The intended first-run experience is
deliberately simple and uses **only mocks** (mock LLM provider, mock tool/MCP
layer, simple YAML and prompts, no real external services), so users grasp the
product quickly:

```bash
git clone <repo> && cd <repo>
make install

# CLI working name: agentctl
agentctl validate examples/hello-agent/agent.yml
agentctl graph    examples/hello-agent/agent.yml
agentctl run      examples/hello-agent/agent.yml --message "hello"
agentctl serve    examples/hello-agent/agent.yml
```

| Command    | Phase            | Purpose                                            |
| ---------- | ---------------- | -------------------------------------------------- |
| `validate` | build            | Load + validate the YAML; report what is valid.    |
| `graph`    | build            | Compile and print the `CompiledAgentGraph`.        |
| `run`      | runtime (local)  | Execute one request locally (mock LLM/tools).      |
| `serve`    | runtime (server) | Start the API and serve requests.                  |

The long-term promise: *define your agent system in YAML, run it locally, extend
it with resolvers or sidecars, deploy it when ready.* Real LLMs, MCP, sidecar, and
deployment come later — see [ROADMAP.md](ROADMAP.md).

---

## First implementation step

Architecture is documented; **no runtime exists yet**. The first implementation
work is the repository/package foundation, then — as the **first feature** — the
**YAML schema & validation** layer (not runtime, MCP, sidecar, or API):

1. `tasks/0001-repository-foundation.md` — create the `src/agentplatform/`
   package skeleton and test layout.
2. `tasks/0002-yaml-schema-and-validation.md` — Pydantic models, YAML loader, and
   validation: definitions, hierarchy, reusable agent instances, prompt paths,
   tool/MCP/resolver references, no-hardcoded-secrets, plus tests.

The first successful command should be `agentctl validate examples/hello-agent/agent.yml`,
producing output such as:

```text
✓ YAML loaded
✓ Definitions valid
✓ Hierarchy valid
✓ Reusable agent instances valid
✓ Prompts valid
✓ Tools valid
✓ MCP servers valid
```

Subsequent tasks build the compiler (`0003`), runtime engine (`0004`), prompt
rendering (`0005`), sidecar/resolvers (`0006`), and tools/permissions (`0007`).
Work proceeds task-by-task; do not implement ahead of the current task's scope.
