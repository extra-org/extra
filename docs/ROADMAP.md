# Roadmap

A phased, honest plan. Each phase below maps to one or more task files in
`tasks/`. Status reflects reality, not aspiration.

> **Package layout note:** task 0001 planned a single `src/agentplatform/`
> package. The implementation instead split into two packages — `agent_engine`
> (pure execution) and `agent_manager` (conversation/session persistence + HTTP
> API, built on top of `agent_engine`) — plus `agentctl` (CLI). Phases below
> map to the numbered tasks as originally scoped; several phases were also
> extended well beyond their task's original scope (Bedrock, runtime hooks,
> execution limits, conversation persistence, the embeddable widget) — see the
> "Beyond the original 12 tasks" section below for the parts no task number
> covers.

| Phase | Theme                         | Tasks      | Status        |
| ----- | ----------------------------- | ---------- | ------------- |
| 0     | Repository foundation         | docs/.ai/tasks | ✅ done      |
| 1     | Package skeleton & tooling    | 0001       | ✅ done (as `agent_engine`/`agent_manager`/`agentctl`, not `agentplatform`) |
| 2     | YAML schema & validation      | 0002       | ✅ done        |
| 3     | Compiled agent graph          | 0003       | ✅ done        |
| 4     | Runtime engine                | 0004       | ✅ done        |
| 5     | Prompt rendering              | 0005       | 🔶 partial     |
| 6     | Plugin context/access         | 0006       | 🔶 partial — access control not actually enforced yet |
| 7     | MCP & plugin tools            | 0007       | ✅ done        |
| 8     | CLI                           | 0008       | ✅ done        |
| 9     | API server                    | 0009       | ✅ done (two layers — see below) |
| 10    | Docker / deployment           | 0010       | ✅ done (basic container, as scoped) |
| 11    | Observability & tracing       | 0011       | 🔶 partial     |
| 12    | Tests & quality gates         | 0012       | 🔶 partial (large existing suite; dedicated hardening pass not done) |

## Beyond the original 12 tasks

These shipped without a dedicated numbered task and are not reflected in the
table above:

| Capability | Status | Where |
| ---------- | ------ | ----- |
| Runtime hooks (auth/policy/audit/context-enrichment, incl. `transform_tool_result`) | ✅ done | `agent_engine/runtime/hooks/`, [RUNTIME_HOOKS.md](RUNTIME_HOOKS.md) |
| Per-run execution-limit guardrails | ✅ done | `agent_engine/core/execution.py`, `agent_engine/runtime/execution.py`, [EXECUTION_LIMITS.md](EXECUTION_LIMITS.md) |
| Amazon Bedrock, Google Gemini, and OpenAI model providers (in addition to Anthropic) | ✅ done | `agent_engine/models/factory.py` |
| Any OpenAI-compatible endpoint, with `provider: zai`/`deepseek`/`moonshot`/`groq`/`xai`/`openrouter` shorthand and a `base_url`/`api_key_env` escape hatch for anything else (self-hosted Ollama/vLLM, an unlisted vendor, an internal proxy) | ✅ done | `agent_engine/models/factory.py`, `agent_engine/models/presets.py`, [YAML_SPEC.md](YAML_SPEC.md#any-openai-compatible-endpoint) |
| Conversation persistence (SQLite default, sessions, users) | ✅ done | `agent_manager/` |
| Embeddable JS/React chat widget | ✅ done | `agent_manager/api/static/widget/` |
| Long-term / cross-conversation memory | ⏳ planned | — |

## Open-source developer-experience milestones

The platform is **self-hosted open source**. The bundled `examples/enterprise-knowledge-assistant/agents.yaml`
demonstrates the product end-to-end. The CLI (**`agentctl`**) unlocks these
milestones in order:

| Milestone | Command (on `examples/enterprise-knowledge-assistant/agents.yaml`) | Enabled by | Status |
| --------- | ---------------------------------- | ---------- | ------ |
| Validate  | `agentctl validate examples/enterprise-knowledge-assistant/agents.yaml` | 0002 | ✅ done |
| Inspect   | `agentctl inspect examples/enterprise-knowledge-assistant/agents.yaml` | 0003 | ✅ done |
| Generate  | `agentctl generate examples/enterprise-knowledge-assistant/agents.yaml --mode all` | 0006 | ✅ done |
| Run local | `agentctl run examples/enterprise-knowledge-assistant/agents.yaml --message "hello"` | 0004–0006 | ✅ done |
| Serve     | `agentctl serve examples/enterprise-knowledge-assistant/agents.yaml` | 0009 | ✅ done |
| Chat      | `agentctl chat examples/enterprise-knowledge-assistant/agents.yaml` | 0004–0006 | ✅ done |

All six commands work today. The graph-inspection command originally planned
as `agentctl graph` shipped as `agentctl inspect` (an offline text summary,
not a visual graph dump).

## Phase detail: what "partial" means

**Phase 3 — Compiled agent graph (✅ done).** `compiler/compile.py` compiles a
validated spec into an immutable `CompiledAgentGraph`. Node declarations
(orchestrators and agents) are built with resolved model, resolver, tool, and
MCP bindings. The `graph` tree is expanded into `AgentNode` objects with stable
node paths. Tests cover compilation and node path generation.

**Phase 4 — Runtime engine (✅ done).** `RuntimeEngine` (via `Engine`) is
created once from a `LoadedSpec`. `ExecutionContext` is created per request.
LangGraph-based routing recurses through orchestrators to leaf agents. Agents
call LLMs with tools bound. Tests cover routing with mock LLM factories.

**Phase 5 — Prompt rendering (🔶 partial).** Prompt file loading and simple
`{{ variable }}` substitution work inside `langgraph_builder.py`. Resolver
values are injected per request. **Remaining:** a dedicated `prompts/` module
with a parsed-template cache, strict missing-variable errors, and a formal
renderer interface.

**Phase 6 — Plugin context/access (🔶 partial).** Resolver plugins are fully
implemented: TOML-configured per-agent resolver classes, dynamic loading,
`BaseResolver` + per-agent subclasses, shared/agent-scoped resolvers, generation
modes (`--mode all/children/child`), overwrite protection, stale detection.
The access plugin **is wired into routing** — `AccessFilter`
(`agent_engine/engine/langgraph/filters.py`) runs during orchestrator child
filtering — but the request-context gate that should populate it with real
identity/permissions was never built, so `AccessFilter` always filters against
an empty `run_context`. **Remaining:** implement the Security/Context Gate so
`protected` nodes are actually enforced, not just structurally filterable.

**Phase 7 — MCP & plugin tools (✅ done).** Python plugin tools load from
`plugins/tools/` and are bound to agents at graph-build time. Tool-call loops
work. MCP servers (local and remote) connect via `langchain-mcp-adapters`
(`MultiServerMCPClient`) and discover tools at build time; unreachable servers
are logged and skipped. Authenticated MCP is supported via runtime hooks
(`agent_engine/loaders/mcp_auth_loader.py`). **Remaining (tracked separately,
not blocking):** per-tool `input_policy` / trusted-parameter injection.

**Phase 8 — CLI (✅ done).** `agentctl validate`, `agentctl inspect`, `agentctl
generate` (with `--mode`, `--agent`, `--force`), `agentctl run` (with
`--stream`, `--session-id`, `--user-id`), `agentctl serve`, and `agentctl chat`
are all implemented. The originally planned `agentctl graph` shipped as
`agentctl inspect` (text summary, not a visual dump).

**Phase 9 — API server (✅ done).** Two layers exist: `agent_engine/api/app.py`
is a thin, stateless FastAPI app directly over the engine (`/health`,
`/invoke`, `/stream`); `agent_manager/api/` is a conversation-lifecycle API
(`/conversations`, message history, SSE streaming) backed by SQLite
persistence, plus the embeddable JS/React chat widget served as a static
asset.

**Phase 10 — Docker / deployment (✅ done, basic container).** A root
`Dockerfile` + `entrypoint.sh` build an image whose default command is
`agentctl serve`. This matches the roadmap's explicit scope (a basic
container, not production deployment topologies).

**Phase 11 — Observability & tracing (🔶 partial).** `agent_engine/observability/`
wires LangChain callback providers — structured logging and Langfuse — into
the engine, giving basic tracing today. **Remaining:** the formal per-request
trace schema, redaction pipeline, and export path originally scoped for this
phase; test coverage here is currently thin (one test module).

**Phase 12 — Tests & quality gates (🔶 partial).** A substantial pytest suite
already exists (`tests/` spans compiler, engine, cli, e2e, `agent_manager`,
models, observability, and runtime/hooks, among others), built up
incrementally as each phase landed. **Remaining:** the dedicated quality-gate
hardening pass this phase describes has not been done as its own unit of
work.

## Principles guiding the order

- **Validate before compile, compile before run.** The schema/validation work
  (0002) precedes the compiler (0003), which precedes the runtime (0004).
- **Capabilities before surfaces.** Core capabilities (prompts, plugins, tools)
  land before the surfaces that expose them (CLI, API).
- **Deployment and deep observability last**, once there is something to deploy
  and observe.
- **Tests accompany every task**; task 0012 hardens the overall quality gate
  rather than introducing testing for the first time.

## What "done" means per phase

A phase is done when its task's acceptance criteria are met, `make check`
passes, and the relevant documentation is consistent with the implementation.

## Explicitly out of scope (for now)

- Production-grade deployment topologies beyond a basic container.
- A hosted control plane / UI.
- Client-specific auth or business logic (this belongs in client plugins).
- Turning YAML into a general-purpose programming language.
