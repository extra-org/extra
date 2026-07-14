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
> [`SIDECAR_CONTEXT_AUTH.md`](SIDECAR_CONTEXT_AUTH.md), and
> [`MCP_AND_TOOLS.md`](MCP_AND_TOOLS.md).

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
agentctl validate examples/enterprise-knowledge-assistant/agents.yaml
agentctl graph    examples/enterprise-knowledge-assistant/agents.yaml
agentctl run      examples/enterprise-knowledge-assistant/agents.yaml --message "hello"
agentctl serve    examples/enterprise-knowledge-assistant/agents.yaml
```

The long-term promise:

1. **Define** your agent system in YAML.
2. **Run** it locally (against mock LLM/tools first).
3. **Extend** it with resolvers, plugins, or a sidecar.
4. **Deploy** it when ready.

> Status: **active development.** The YAML validator, compiler, runtime engine,
> LangGraph builder, resolver plugin system (shared + agent-scoped), tool
> plugin loading, an MCP client (local and remote servers, including
> authenticated MCP via hooks), a runtime hooks system (11 lifecycle points,
> including `transform_tool_result` — see
> [RUNTIME_HOOKS.md](RUNTIME_HOOKS.md)), per-run execution-limit guardrails
> (see [EXECUTION_LIMITS.md](EXECUTION_LIMITS.md)), prompt file rendering, and
> the CLI (`validate`, `inspect`, `generate`, `run`, `serve`, `chat`) are
> **implemented**. Model access supports Anthropic and Amazon Bedrock. Two
> HTTP API layers exist: a thin stateless API directly over the engine
> (`/invoke`, `/stream` in `agent_engine`) and a conversation-lifecycle service
> built on top of it (`agent_manager`) with SQLite-backed persistence, SSE
> streaming, and an embeddable JS/React chat widget. Basic observability
> (structured logging + a Langfuse callback provider) is wired in, and a
> `Dockerfile` provides a basic container image. The access plugin **is**
> wired into child filtering, but the request-context gate that should feed it
> real identity/permissions is **not yet implemented** — access filtering
> currently runs against an empty context, so `protected` nodes are not
> actually enforced yet. A formal structured tracing/observability schema and
> long-term/cross-conversation memory are also **not yet implemented**. See
> [ROADMAP.md](ROADMAP.md) for per-phase status.

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
  → Supervisor Execution   (orchestrators call children as tools; agents execute)
  → Tool / Data Enforcement(trusted parameters; policy at the tool layer)
  → MCP / Tool Calls       (adapters behind the runtime)
  → Response + Trace
```

Two distinct phases sit inside this flow: a **build/compilation phase** that runs
once before serving, and a **runtime/execution phase** that runs per request.

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
- Generate **resolver/plugin stubs** for the client to fill in
  (`agentctl generate` — ✅ implemented with generation modes and overwrite
  protection), and *(later)* generate **deployment artifacts**.

The runtime only ever sees the compiled, typed model — never raw YAML dicts.

---

## 4. Runtime / Execution Phase

This phase happens **per request**. It answers:

> *A request arrived. Which node should handle it, with what context and
> permissions?*

Responsibilities:

- **Receive** the request. The core engine API (`agent_engine`, `/invoke` and
  `/stream`) is stateless per call — it takes headers and a complete
  `messages` array and owns no conversation memory. The optional
  `agent_manager` layer sits in front of it and **does** own conversation
  memory: it persists messages per conversation/session (SQLite by default),
  assembles prior context before calling the engine, and exposes
  `/conversations` endpoints and SSE streaming
  (see `src/agent_manager/application/service.py`). `RUNTIME_LIFECYCLE.md`
  currently documents only the stateless engine path; it has not yet been
  updated to describe this composition.
- **Create** a fresh `ExecutionContext` for this request.
- **Build `ctx`** from request headers and request data.
- **Resolve identity/context/permissions** via the extension layer (in-process
  plugins and/or a client-owned sidecar) — the runtime does not interpret tokens
  or roles itself.
- **Security / context gate**: filter `protected` nodes via the access plugin so
  an orchestrator never exposes a child the caller may not reach.
- **Execute the root node as a supervisor agent**: the orchestrator's children
  (agents or nested orchestrators) are exposed to it as callable tools; it
  decides which child tool(s) to call, collects their answers, and synthesises a
  final response. The whole tree runs inside the root invocation.
- **Resolve dynamic prompt values** by calling each node's declared resolvers.
- **Render prompt templates** for this request (strict; missing variables fail).
- **Execute leaf agents** with only their declared tools bound.
- **Enforce tool/data policy**: expose only the agent's declared tools; pass
  trusted, runtime-controlled context to tools (see §11).
- **Call tools / MCP servers** through runtime-managed adapters.
- **Return** the response plus a structured trace (`visited` = call-chain;
  `used_tools` = real tool/MCP calls, merged up from nested agents).

---

## 5. Client Extension Layer

Client-specific logic **must not** be hardcoded into the generic runtime. It belongs
in client-owned extension points:

- **Resolver plugins** — in-process Python methods that produce prompt values.
- **Tool plugins** — in-process Python methods exposed to the LLM as actions.
- **Access plugin** — a fixed `plugins/access.py` that decides protected-node
  access.
- **Plugin packages** — the client's own Python package holding shared state
  (DB pools, REST clients, auth clients, caches).
- **Sidecar service** *(optional / future)* — an out-of-process,
  client-owned service for stronger isolation or non-Python logic.

Examples of what lives in the extension layer (never in the runtime):

- authentication and authorization
- `organization_id` / tenant / user lookups
- permission and role lookups
- database queries and third-party API calls
- business-specific context
- (future) tool input policies

The runtime's job is to **call these extension points through fixed contracts**,
map their results into the `ExecutionContext`, and **enforce** the outcomes —
not to contain the business logic itself.

---

## 6. RuntimeEngine vs. ExecutionContext

This separation is mandatory.

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
- `organization_id` and other resolved context values
- the selected agent node path
- rendered prompt values for this request
- temporary tool results
- trace events
- errors

Request-scoped data never leaks onto the `RuntimeEngine` or the compiled graph.

---

## 7. Node Declarations vs. Agent Nodes

A node (orchestrator or agent) is **declared once** under `orchestrators:` /
`agents:` and may be **reused** at multiple locations in the `graph`.

- The **NodeDeclaration** is the reusable declaration (its prompts, model,
  tools, resolvers, etc.).
- An **AgentNode** is one concrete node inside the compiled graph.

A single `security_review_agent` definition may, for example, appear under both
a documentation flow and an analysis flow. Conceptually:

```text
agents:
  security_review_agent:        # one reusable definition
    description: "Reviews a request for policy/security issues."
    prompts:
      system: "prompts/security_review/system.md"

graph:
  root_orchestrator:
    knowledge_router:
      security_review_agent:    # AgentNode A (under knowledge_router)
    analysis_router:
      security_review_agent:    # AgentNode B (under analysis_router)
```

Here:

- `security_review_agent` is the reusable **NodeDeclaration**.
- The occurrence under `knowledge_router` is one **AgentNode**.
- The occurrence under `analysis_router` is another **AgentNode**.
- Both point to the same `NodeDeclaration`.

The compiler assigns each occurrence a **stable node path**, and the trace
includes **both the node/agent id and the node path**, so two occurrences of the
same declaration are distinguishable in observability.

---

## 8. Dynamic Prompt Rendering

Prompt files are **templates**, not finished text.

- **Raw/parsed templates may be cached** (keyed by path/version).
- **Rendered prompts are produced per request** — never rendered globally at
  startup and never cached across requests/tenants.
- Values such as `organization_id`, `current_date`, `tenant_id`, `permissions`,
  user profile, `locale`, and `region` are **dynamic**, resolved per request.
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
- a generated resolver class method
- a plugin dependency configured outside the runtime

In the current model, resolvers are **in-process Python plugin methods** chosen
by the engine (not exposed to the LLM) and run **before** a node executes:

```yaml
resolvers:
  current_date:
    scope: shared        # generated once in SharedResolver
  subscription:
    scope: agent         # generated in each agent's resolver subclass

agents:
  super_agent:
    description: "Handle supermarket orders."
    prompts:
      system: "prompts/super/system.md"
    resolvers: [current_date, subscription]
```

Each resolver has a **scope**: `shared` (generated on `SharedResolver`, inherited
by all agents) or `agent` (generated only on the declaring agent's resolver
subclass). Scope is validated at YAML load time.

Resolver classes are loaded by **file path** (one file per agent). Their
importable refs are catalogued in the single plugin manifest
`plugins/plugins.toml` — one manifest for hooks, resolvers, and tools — which is
a documentation/generation artifact, not a runtime input (see
[RUNTIME_HOOKS.md](RUNTIME_HOOKS.md) → "The `plugins.toml` manifest"):

```toml
[resolvers]
shared = "plugins.resolvers.shared:SharedResolver"
super_agent = "plugins.resolvers.repository_agent:Resolver"
```

### Resolver file layout

Resolver stubs are generated into a **file-per-class** layout under the unified
plugin package:

```text
plugins/
  __init__.py
  plugins.toml                   # single manifest (hooks + resolvers + tools)
  resolvers/
    __init__.py
    shared.py                    # SharedResolver with shared methods
    domestic_flights_agent.py    # per-agent Resolver subclass
    international_flights_agent.py
    super_agent.py
```

### Resolver generation

`agentctl generate` creates resolver stubs with three modes:

| Mode | Command | Effect |
| ---- | ------- | ------ |
| `all` | `agentctl generate agents.yml --mode all` | Regenerate `shared.py`, all agent files, and TOML |
| `children` | `agentctl generate agents.yml --mode children` | Generate/update agent files only; skip `shared.py` |
| `child` | `agentctl generate agents.yml --mode child --agent super_agent` | Generate/update one agent file only |

By default, existing client implementations are preserved — only missing
method stubs are appended. Use `--force` to overwrite. Stale files and methods
(scope changes, removed resolvers) are reported but never deleted automatically.

### Runtime resolution

1. The runtime loads the selected agent's resolver class from TOML.
2. The resolver class is instantiated once per loader (with configured
   dependencies).
3. Resolver methods are called by name; shared methods resolve through normal
   Python inheritance from `SharedResolver`.
4. Resolver outputs are request-scoped and land on `ExecutionContext`.
5. The runtime is generic — it has no knowledge of resolver business logic.

---

## 10. Sidecar Model

A **sidecar** is a **client-owned out-of-process service** that supplies
client-specific behavior the runtime must not contain:

- authentication / authorization
- identity, tenant, and `organization_id` resolution
- permission lookups
- DB / API lookups
- business context
- (future) tool policy

When used, the runtime calls the sidecar through a **standard contract**, maps
the response into the `ExecutionContext`, and **still owns enforcement** — the
sidecar advises; the runtime enforces.

> Current status: the MVP extension mechanism is **in-process plugins** (§5, §9).
> A separate sidecar service is a documented **future option** that would be
> introduced with its own design note and schema additions
> (see [`SIDECAR_CONTEXT_AUTH.md`](SIDECAR_CONTEXT_AUTH.md)). The sidecar and
> plugin models share the same principle: client logic lives outside the runtime
> behind a fixed contract.

---

## 11. Security Model

> **Prompt text is not a security boundary.**

Wording like this is **not** enforcement:

```text
Only answer for organization {{ organization_id }}
```

A capable model can be steered around prompt instructions, so security must be
enforced at the **tool/data layer**, where the runtime controls the actual
parameters and access.

The principle, expressed conceptually:

```yaml
# Conceptual / future schema — per-tool input policy is NOT yet in the schema.
tools:
  enterprise_docs_search:
    input_policy:
      inject:
        organization_id: "{{ identity.organization_id }}"
      block_user_override:
        - organization_id
```

The intended guarantees:

- the LLM **cannot choose** `organization_id`;
- the user **cannot override** `organization_id`;
- the runtime **injects** the trusted `organization_id` from resolved identity;
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
- a **simple YAML** config (the bundled [`examples/enterprise-knowledge-assistant/agents.yaml`](../examples/enterprise-knowledge-assistant/agents.yaml))
- **simple prompts**

First-run flow status:

```bash
make install
agentctl validate examples/enterprise-knowledge-assistant/agents.yaml                            # ✅ implemented
agentctl inspect  examples/enterprise-knowledge-assistant/agents.yaml                             # ✅ implemented (offline summary: agents, MCPs, hooks, plugins, tags)
agentctl generate examples/enterprise-knowledge-assistant/agents.yaml --mode all                 # ✅ implemented
agentctl run      examples/enterprise-knowledge-assistant/agents.yaml --message "hello"          # ✅ implemented (requires LLM API key)
agentctl serve    examples/enterprise-knowledge-assistant/agents.yaml                             # ✅ implemented (HTTP API; also the Docker CMD)
agentctl chat     examples/enterprise-knowledge-assistant/agents.yaml                             # ✅ implemented (interactive session)
```

All six CLI commands work today. `agentctl graph` from earlier plans shipped
as `agentctl inspect`. See [`ROADMAP.md`](ROADMAP.md).

---

## 14. Implementation Status

The following layers are implemented:

**Spec & validation (0002 — ✅ done):**
Pydantic models, safe YAML loader, JSON Schema validation, semantic validation
(graph, resolver/tool/MCP references, prompt paths, secrets, resolver scope).

**Compiler (0003 — ✅ done):**
Compiles validated spec into an immutable `CompiledAgentGraph` with resolved
node declarations, stable node paths, and model/resolver/tool/MCP bindings.

**Runtime engine (0004 — ✅ done):**
`LangGraphEngine` wraps compile + LangGraph build behind an async context
manager (`build()` / `close()`); per-request state flows as `GraphState`.
Orchestrators are **supervisor agents** — children are exposed as tools and the
orchestrator synthesises the answer; the compiled graph is flat
(`START → root → END`). Both orchestrators and agents run a tool-call loop until
the model stops.
Per-run **execution-limit** guardrails (`ExecutionPolicy` in
`agent_engine/core/execution.py`, enforced by `agent_engine/runtime/execution.py`)
cap iterations, tool calls, and child-agent calls — see
[`EXECUTION_LIMITS.md`](EXECUTION_LIMITS.md).

**Prompt rendering (0005 — 🔶 partial):**
Prompt files are loaded from disk and `{{ variable }}` placeholders are
substituted with resolver values per request. No dedicated `prompts/` module,
parsed-template cache, or strict missing-variable errors yet.

**Resolver plugins & access (0006 — 🔶 partial, resolver side done):**
Full resolver plugin system: TOML-configured `SharedResolver` + per-agent
subclasses, shared/agent-scoped resolvers, dynamic loading, generation modes
(`--mode all/children/child`), overwrite protection, stale detection. The access
plugin **is** wired: an `AccessFilter` (`agent_engine/engine/langgraph/filters.py`)
removes protected children before they are exposed as orchestrator tools.
**Populating the request context it filters on (the Security/Context Gate) is
not yet implemented** — `run_context` is never written into `GraphState`, so
`AccessFilter` always filters against an empty context. Every example access
plugin currently just returns `True`. Treat protected-node access control as
**not actually enforced today**, not as a finished feature.

**Tool plugins, MCP & runtime hooks (0007 — ✅ done):**
Python plugin tools load from `plugins/tools/`, are bound per-agent, and execute
in a tool-call loop. MCP servers connect via `langchain-mcp-adapters`
(`MultiServerMCPClient`), discovering tools at build time; unreachable servers
are logged as a warning and skipped. Both local and remote MCP servers work,
including authenticated MCP via runtime hooks
(`agent_engine/loaders/mcp_auth_loader.py`). A separate **runtime hooks**
system (`agent_engine/runtime/hooks/`,
[`RUNTIME_HOOKS.md`](RUNTIME_HOOKS.md)) covers 11 lifecycle points — engine
start/stop, run start/end/error, tool error, and `transform_tool_result` (lets
trusted code truncate/redact/normalize a tool result before it reaches the
model) — for auth, policy, audit, and context enrichment, distinct from
LLM-invoked tools. Per-tool `input_policy` (trusted parameter injection) is
still not implemented — see §11.

**Model providers (✅ done, not in the original task list):**
`agent_engine/models/factory.py` builds chat models for **Anthropic** (via
`init_chat_model`), **Amazon Bedrock** (`ChatBedrockConverse`), **Google
Gemini** (`ChatGoogleGenerativeAI`), and **OpenAI** (`ChatOpenAI`), with clear
configuration errors for missing settings. The `openai` provider additionally
accepts `base_url` and `api_key_env`, so it also covers any OpenAI-compatible
endpoint — third-party vendors (Z.AI, DeepSeek, Moonshot, Groq, xAI,
OpenRouter, ...) or a self-hosted server (Ollama, vLLM) — without a new
provider integration. See [YAML_SPEC.md](YAML_SPEC.md#any-openai-compatible-endpoint).

**CLI (0008 — ✅ done):**
`agentctl validate`, `agentctl inspect` (offline summary: agents, MCPs, hooks,
plugins, tags), `agentctl generate` (with `--mode`, `--agent`, `--force`),
`agentctl run` (with `--stream`, `--session-id`, `--user-id`), `agentctl serve`,
and `agentctl chat`.

**API server (0009 — ✅ done, two layers):**
`agent_engine/api/app.py` exposes a thin, stateless FastAPI app directly over
the engine (`GET /health`, `POST /invoke`, `POST /stream`), started by
**`agentctl serve`** (default port `8090`) — no persistence, no widget.
`agent_manager/api/` exposes a conversation-lifecycle API on top of it
(`POST /conversations`, `GET/POST .../messages`, `POST .../messages/stream` as
SSE) backed by `agent_manager`'s persistence layer (see next), started by the
separate **`agent-manager`** console script (default port `8100`) — this is
also what serves the embeddable chat widget. `agentctl chat` talks to neither
server by default; in `--url` mode it talks to `agentctl serve`'s stateless
`/invoke`/`/stream` API and does not persist anything.

**Conversation persistence (✅ done, not in the original task list):**
`agent_manager` is a DDD-style service (`domain/`, `application/`,
`infrastructure/persistence/`) providing `ConversationService` (send/stream,
prior-context assembly, post-success persistence), a SQLite-backed
`SqlRepository` with Alembic migrations, and tables for conversations,
messages, users, and sessions. It is wired into both `agentctl run`/`chat`
(CLI) and the `agent_manager` API server. `agent_engine` has no dependency on
`agent_manager`; the boundary is one-directional.

**Embeddable widget (✅ done, not in the original task list):**
`agent_manager/api/static/widget/` is a React-based, embeddable chat widget
(shadcn AI chat primitives) served as a script tag, with streaming support,
conversation recovery, and e2e/accessibility tests. This — not a standalone
`frontend/` app — is the project's web client today.

**Docker / deployment (0010 — ✅ done, basic container):**
A root `Dockerfile` installs the package and runs `entrypoint.sh`, whose
default `CMD` is `agentctl serve --config /workspace/agents.yml` (installing
`/workspace/requirements.txt` if present). Matches the roadmap's explicit
scope of a "basic container," not production deployment topologies.

**Observability (0011 — 🔶 partial):**
`agent_engine/observability/registry.py` wires LangChain `BaseCallbackHandler`
providers — a `LoggingProvider` and a `LangfuseProvider` — into the engine.
This gives basic structured logs and optional Langfuse tracing, but the
formal per-request trace schema, redaction pipeline, and export path
originally scoped in task 0011 do not exist yet, and test coverage is thin
(one test module).

**Tests & quality gates (0012 — 🔶 partial):**
A substantial pytest suite already exists (`tests/` has ~13 subdirectories
covering compiler, engine, cli, e2e, agent_manager, models, observability,
runtime/hooks, and more), but the dedicated quality-gate hardening pass
described by task 0012 has not been done as a discrete unit of work.

**Not yet implemented:** the request-context/Security-Context Gate that
should populate real identity into `AccessFilter` (see above); per-tool
`input_policy` / trusted-parameter injection (§11); a dedicated `prompts/`
module with template caching and strict variable errors; a formal
observability/tracing schema; long-term/cross-conversation memory.

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
