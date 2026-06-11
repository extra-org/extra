# Architecture

This document describes the architecture of the platform end-to-end. It is a
design blueprint; the implementation is built task-by-task from
[`tasks/`](../tasks/).

This project is an **open-source, self-hosted platform for defining and running
AI-agent systems declaratively**. Users describe an agent system in a YAML
specification; the platform validates that specification, compiles it into an
internal executable agent graph, and runs it through a generic runtime engine.

YAML is the **declarative specification layer** of the platform. It describes
what agents, orchestrators, tools, MCP servers, prompts, resolvers, hierarchy,
security requirements, observability, and deployment metadata should exist. The
platform validates and compiles that declaration into an executable runtime
model, and at request time handles context resolution, prompt rendering,
routing, tool-policy enforcement, tracing, and runtime orchestration. The result
is that teams define *what* their agent system should be, and the platform owns
*how* it runs.

> Read this with [`YAML_SPEC.md`](YAML_SPEC.md) (the input contract),
> [`RUNTIME_LIFECYCLE.md`](RUNTIME_LIFECYCLE.md), [`PROMPT_RENDERING.md`](PROMPT_RENDERING.md),
> [`SIDECAR_CONTEXT_AUTH.md`](SIDECAR_CONTEXT_AUTH.md), [`MCP_AND_TOOLS.md`](MCP_AND_TOOLS.md),
> and the ADRs in [`adr/`](adr/).

---

## 1. Product Vision

This project is an **open-source, self-hosted platform for defining and running
AI-agent systems declaratively**.

- Users describe their agent system in YAML (LLM providers, MCP servers, tools,
  resolvers, orchestrators, agents, prompts, and the graph topology).
- The platform **validates** the YAML, **compiles** it into an internal graph,
  and **runs** the system through a generic runtime.
- Client-specific logic (auth, tenancy, business data) is supplied through
  **extension points** — in-process plugins and, optionally, a client-owned
  sidecar — never hardcoded into the runtime.

The intended developer experience is a small CLI (working name `agentctl`).
Once the corresponding phases land, a user should be able to run:

```bash
agentctl validate examples/agents.yml
agentctl graph    examples/agents.yml
agentctl run      examples/agents.yml --message "hello"
agentctl serve    examples/agents.yml
```

The long-term promise:

1. **Define** your agent system in YAML.
2. **Run** it locally (against mock LLM/tools first).
3. **Extend** it with resolvers, plugins, or a sidecar.
4. **Deploy** it when ready.

> Status: **foundation phase.** The runtime, compiler, validator, prompt
> renderer, plugins, MCP, API, and CLI are **not implemented yet**. This document
> describes the intended design; nothing here should be read as "already works."

---

## 2. High-Level Architecture Flow

YAML is the input at the top; a response plus trace is the output at the bottom.
Everything in between is the platform.

```text
agents.yml (declarative input specification)
  → YAML Loader            (safe load to plain data)
  → Validator              (schema + semantic validation)
  → Compiler               (typed, normalized model)
  → CompiledAgentGraph     (immutable, executable graph)
  → RuntimeEngine          (created once at startup; long-lived)
  → ExecutionContext       (created per request)
  → Security / Context Gate(protected-node access filtering)
  → Resolver / Sidecar     (dynamic context resolution)
  → Prompt Rendering       (templates rendered per request)
  → Recursive Execution    (route orchestrators → execute agents)
  → Tool / Data Enforcement(trusted parameters; policy at the tool layer)
  → MCP / Tool Calls       (adapters behind the runtime)
  → Response + Trace
```

Two distinct phases sit inside this flow: a **build/compilation phase** that runs
once before serving, and a **runtime/execution phase** that runs per request
(see [ADR 0007](adr/0007-build-phase-separate-from-runtime-phase.md)).

---

## 3. Build / Generation / Compilation Phase

This phase happens **before any request is served**. It answers two questions:

> *Is this declared agent system valid? What executable graph should it produce?*

Responsibilities:

- **Load** the YAML safely (no code execution, no arbitrary object construction).
- **Validate schema** against the JSON Schema
  ([`examples/config.schema.json`](../examples/config.schema.json)).
- **Validate references**: every agent/orchestrator `tools`, `mcps`, and
  `resolvers` id exists in the corresponding top-level declaration.
- **Validate prompt paths**: declared prompt files exist and are readable.
- **Validate tools / MCP**: tool plugin `class`/`method` references and MCP
  server declarations are well-formed.
- **Validate the graph**: every node referenced in `graph` is declared; there is
  a single root; routing structure is consistent.
- **Validate reusable agent nodes**: a node declared once may appear at multiple
  graph locations; each occurrence is a distinct `AgentNode` (see §7).
- **Detect cycles** in the graph.
- **Detect secrets**: reject literal secrets in YAML; only references are allowed.
- **Compile** the flat declarations plus `graph` topology into a typed,
  immutable `CompiledAgentGraph`, assigning a **stable node path** to each
  occurrence in the graph.
- *(Optional, later)* generate **resolver/plugin stubs** for the client to fill
  in, and *(later)* generate **deployment artifacts**.

The runtime only ever sees the compiled, typed model — never raw YAML dicts
(see [ADR 0002](adr/0002-yaml-is-compiled-not-executed-directly.md)).

---

## 4. Runtime / Execution Phase

This phase happens **per request**. It answers:

> *A request arrived. Which node should handle it, with what context and
> permissions?*

Responsibilities:

- **Receive** the request (`POST /api/invoke` with headers and a complete
  `messages` array — the platform does not own conversation memory).
- **Create** a fresh `ExecutionContext` for this request.
- **Build `ctx`** from request headers and request data.
- **Resolve identity/context/permissions** via the extension layer (in-process
  plugins and/or a client-owned sidecar) — the runtime does not interpret tokens
  or roles itself.
- **Security / context gate**: filter `protected` nodes via the access plugin so
  the router never considers nodes the caller may not reach.
- **Route** from the root node down through orchestrators, using each child's
  `description` and the orchestrator prompt.
- **Select** the `AgentNode` to execute.
- **Resolve dynamic prompt values** by calling the node's declared resolvers.
- **Render prompt templates** for this request (strict; missing variables fail).
- **Execute** the selected agent (or continue routing through an orchestrator).
- **Enforce tool/data policy**: expose only the agent's declared tools; pass
  trusted, runtime-controlled context to tools (see §11).
- **Call tools / MCP servers** through runtime-managed adapters.
- **Return** the response plus a structured trace.

---

## 5. Client Extension Layer

Client-specific logic **must not** be hardcoded into the generic runtime
(see [ADR 0003](adr/0003-client-specific-logic-lives-in-sidecar.md)). It belongs
in client-owned extension points:

- **Resolver plugins** — in-process Python methods that produce prompt values.
- **Tool plugins** — in-process Python methods exposed to the LLM as actions.
- **Access plugin** — a fixed `plugins/access.py` that decides protected-node
  access.
- **Plugin packages** — the customer's own Python package holding shared state
  (DB pools, REST clients, auth clients, caches).
- **Sidecar service** *(optional / future, ADR-gated)* — an out-of-process,
  client-owned service for stronger isolation or non-Python logic.

Examples of what lives in the extension layer (never in the runtime):

- authentication and authorization
- `customer_code` / tenant / user lookups
- permission and role lookups
- database queries and third-party API calls
- business-specific context
- (future) tool input policies

The runtime's job is to **call these extension points through fixed contracts**,
map their results into the `ExecutionContext`, and **enforce** the outcomes —
not to contain the business logic itself.

---

## 6. RuntimeEngine vs. ExecutionContext

This separation is mandatory (see
[ADR 0001](adr/0001-runtime-engine-created-once.md)).

> **RuntimeEngine = how the system works. ExecutionContext = what is happening in
> this specific request.**

### RuntimeEngine (created once, at application startup)

Owns long-lived, request-independent infrastructure:

- the compiled agent graph (immutable)
- prompt template loader
- prompt renderer
- resolver registry / plugin registry
- access plugin
- tool registry
- MCP connection manager
- LLM provider registry
- observability / tracing infrastructure
- runtime configuration

It **must not** store request-specific state. It is safe to share read-only
across concurrent requests.

### ExecutionContext (created once, per request)

Owns everything specific to a single request:

- `request_id`
- the incoming `messages`
- `ctx` built from headers/request data
- identity / tenant / user / session (as interpreted by plugins)
- roles / permissions (as interpreted by plugins)
- `customer_code` and other resolved context values
- the selected agent node path
- rendered prompt values for this request
- temporary tool results
- trace events
- errors

Request-scoped data never leaks onto the `RuntimeEngine` or the compiled graph.

---

## 7. Node Declarations vs. Agent Nodes

A node (orchestrator or agent) is **declared once** under `orchestrators:` /
`agents:` and may be **reused** at multiple locations in the `graph`
(see [ADR 0006](adr/0006-reusable-agent-definitions-and-hierarchy-instances.md)).

- The **NodeDeclaration** is the reusable declaration (its prompts, model,
  tools, resolvers, etc.).
- An **AgentNode** is one concrete node inside the compiled graph.

A single `security_review_agent` definition may, for example, appear under both
an invoice flow and a payment flow. Conceptually:

```text
agents:
  security_review_agent:        # one reusable definition
    description: "Reviews a request for policy/security issues."
    prompts:
      system: "prompts/security_review/system.md"

graph:
  root_orchestrator:
    invoice_orchestrator:
      security_review_agent:    # AgentNode A (under invoices)
    payment_orchestrator:
      security_review_agent:    # AgentNode B (under payments)
```

Here:

- `security_review_agent` is the reusable **NodeDeclaration**.
- The occurrence under `invoice_orchestrator` is one **AgentNode**.
- The occurrence under `payment_orchestrator` is another **AgentNode**.
- Both point to the same `NodeDeclaration`.

The compiler assigns each occurrence a **stable node path**, and the trace
includes **both the node/agent id and the node path**, so two occurrences of the
same declaration are distinguishable in observability.

---

## 8. Dynamic Prompt Rendering

Prompt files are **templates**, not finished text
(see [ADR 0004](adr/0004-prompts-are-templates-rendered-per-request.md) and
[ADR 0005](adr/0005-prompt-rendering-and-context-resolution.md)).

- **Raw/parsed templates may be cached** (keyed by path/version).
- **Rendered prompts are produced per request** — never rendered globally at
  startup and never cached across requests/tenants.
- Values such as `customer_code`, `current_date`, `tenant_id`, `permissions`,
  customer profile, `locale`, and `region` are **dynamic**, resolved per request.
- Rendering is **strict by default**: a missing required variable fails clearly
  with a named error rather than silently emitting a blank.

Prompt text is **not** a security boundary (see §11).

---

## 9. Resolver Model

Resolvers fill the dynamic values a prompt needs. The YAML declares **what**
values a node needs and **where** they come from; the **business logic** that
produces them lives in the extension layer, not in the core runtime.

Dynamic values may originate from:

- the request body
- request headers
- JWT / auth claims
- identity context
- system time
- a database lookup
- a third-party API
- an MCP tool
- a sidecar (optional/future)
- a generated resolver function
- a plugin

In the current model, resolvers are **in-process Python plugin methods** chosen
by the engine (not exposed to the LLM) and run **before** a node executes:

```yaml
resolvers:
  current_date:
    class: Resolvers
    method: current_date

agents:
  super_agent:
    description: "Handle supermarket orders."
    prompts:
      system: "prompts/super/system.md"
    resolvers: [current_date]
```

Resolver outputs are request-scoped and land on the `ExecutionContext`.

---

## 10. Sidecar Model

A **sidecar** is a **client-owned out-of-process service** that supplies
client-specific behavior the runtime must not contain:

- authentication / authorization
- identity, tenant, and `customer_code` resolution
- permission lookups
- DB / API lookups
- business context
- (future) tool policy

When used, the runtime calls the sidecar through a **standard contract**, maps
the response into the `ExecutionContext`, and **still owns enforcement** — the
sidecar advises; the runtime enforces.

> Current status: the MVP extension mechanism is **in-process plugins** (§5, §9).
> A separate sidecar service is a documented **future option** that would be
> introduced with its own ADR and schema additions
> (see [`SIDECAR_CONTEXT_AUTH.md`](SIDECAR_CONTEXT_AUTH.md)). The sidecar and
> plugin models share the same principle: client logic lives outside the runtime
> behind a fixed contract.

---

## 11. Security Model

> **Prompt text is not a security boundary.**

Wording like this is **not** enforcement:

```text
Only answer for customer {{ customer_code }}
```

A capable model can be steered around prompt instructions, so security must be
enforced at the **tool/data layer**, where the runtime controls the actual
parameters and access.

The principle, expressed conceptually:

```yaml
# Conceptual / future schema — per-tool input policy is NOT yet in the schema.
tools:
  invoice_search:
    input_policy:
      inject:
        customer_code: "{{ identity.customer_code }}"
      block_user_override:
        - customer_code
```

The intended guarantees:

- the LLM **cannot choose** `customer_code`;
- the user **cannot override** `customer_code`;
- the runtime **injects** the trusted `customer_code` from resolved identity;
- tool execution is **constrained by runtime policy**, not by prompt wording.

What is enforced **today** vs. **later**:

- **MVP target:** protected-node access filtering via the access
  plugin; trusted context passed to tools via `ctx`; validation of tool/MCP ids;
  secrets kept out of YAML and redacted in traces.
- **Later (deliberate schema addition):** per-tool `input_policy` /
  `block_user_override`. The current schema does **not** define per-tool
  permissions yet (see [`MCP_AND_TOOLS.md`](MCP_AND_TOOLS.md)); this section
  records the direction, not shipped behavior.

---

## 12. Tool and MCP Model

- Agents **declare** the tools and MCP servers they may use; they do **not** open
  MCP connections themselves.
- The **runtime owns MCP connection management** — connections are created once
  on the long-lived engine and shared, never per request.
- The **runtime owns tool enforcement** — it exposes only an agent's declared
  tools and passes trusted request context (`ctx`) into tool calls.
- The **runtime injects protected/trusted parameters** (the enforcement point for
  §11), so model- or user-supplied values cannot override them.
- **Tools and MCP servers are adapters behind runtime boundaries.** Core routing
  and execution logic does not call external systems directly.

Tools are in-process Python plugin methods exposed to the LLM during execution;
MCP servers are external (any language) and connected from the runtime. The
resolver-vs-tool boundary (engine-chosen context vs. LLM-chosen actions) is
detailed in [`MCP_AND_TOOLS.md`](MCP_AND_TOOLS.md).

---

## 13. Open-Source, Self-Hosted Developer Experience

The first experience must be **simple** and runnable with **mocks only** — no
real external services:

- a **mock LLM provider**
- **mock tools / MCP**
- a **simple YAML** config (the bundled [`examples/agents.yml`](../examples/agents.yml))
- **simple prompts**

First-run flow status:

```bash
make install
agentctl validate examples/agents.yml                  # implemented
agentctl graph    examples/agents.yml                  # planned
agentctl run      examples/agents.yml --message "hello" # planned
```

Real LLMs, real MCP servers, real customer plugins, and deployment come **after**
the mock-based local experience works. See [`ROADMAP.md`](ROADMAP.md).

---

## 14. First Implemented Layer

The first implemented product layer is **YAML schema and validation**
(task [`0002`](../tasks/0002-yaml-schema-and-validation.md)). It validates
configuration only — **not** the runtime, MCP, sidecar, real LLM, or deployment.

Validation covers:

- Pydantic models for the spec;
- a safe YAML loader;
- **definitions** validation (providers, MCP servers, tools, resolvers, agents,
  orchestrators, prompts);
- **graph** validation (single root, declared nodes, consistent structure);
- repeated graph occurrence detection groundwork for future node paths;
- **prompt path** validation;
- **tool / MCP reference** validation;
- **resolver reference** validation;
- **no-hardcoded-secrets** validation;
- tests for valid and invalid specs.

It uses [`examples/agents.yml`](../examples/agents.yml) as a valid fixture and
[`examples/config.schema.json`](../examples/config.schema.json) as the schema
source of truth, then adds semantic checks the JSON Schema cannot express
cleanly.

---

## YAML: the declarative specification layer

YAML is the **declarative source of truth for configuration**: it describes the
desired agent system, and the platform validates and compiles that declaration
into the runtime structures described above. The full grammar lives in
[`YAML_SPEC.md`](YAML_SPEC.md); here is how it fits the architecture.

A config declares:

- LLM providers / default model settings
- MCP servers
- tools
- agents and orchestrators
- prompt paths
- context requirements (resolvers)
- resolver contracts
- security requirements (e.g. `protected` nodes)
- the graph (hierarchy/topology)
- observability and deployment metadata (as those layers land)

It has **two parts**:

### Definitions — *what exists*

Flat sections declare the reusable things: `system`/`defaults`, `mcps`, `tools`,
`resolvers`, `orchestrators`, and `agents` (with their prompts, models, and
declared capabilities).

### Graph (hierarchy) — *how it is connected*

The `graph` section declares how nodes are nested and connected, by indentation:

```yaml
graph:
  main_router:
    flights_router:
      domestic_flights_agent:
      international_flights_agent:
    super_agent:
    admin_agent:
```

> **Definitions answer: *what exists?* The graph answers: *how is it
> connected?*** A node id declared in definitions may appear at multiple graph
> locations; each occurrence becomes a distinct `AgentNode` (§7).

---

## Node Model (summary)

- **Orchestrators** are routers: a `description`, a mandatory
  `prompts.orchestrator`, optional `prompts.system`/`prompts.user`, optional
  `model`/`resolvers`/`protected`. They route among their graph children using
  each child's `description`.
- **Agents** are executors: a `description`, optional prompt files, optional
  `resolvers`, `tools`, `mcps`, `model`, and `protected`. For the MVP, agents are
  normally leaves.

---

## Layers (implementation map)

1. **Spec layer** — safe YAML loading and typed schema models.
2. **Validation layer** — JSON Schema + semantic validation.
3. **Compiler layer** — normalized typed graph, bindings, stable node paths.
4. **Runtime layer** — long-lived engine + per-request execution contexts.
5. **Prompt rendering layer** — file templates rendered per request.
6. **Extension layer** — resolver, tool, and access plugins (and optional sidecar).
7. **MCP/tool layer** — MCP client setup and tool execution adapters.
8. **Observability layer** — routing, prompt, resolver, access, tool, and
   response trace events (with redaction).
9. **API / CLI layers** — validate, inspect graph, run locally, and serve.
10. **Deployment layer** — container/base-image packaging.

Each layer depends only on the typed outputs of the layers before it; data flows
one direction (YAML → validate → compile → graph → runtime → prompts/context/tools
→ response + trace).
