# Agent Engine

> A declarative Python engine for hierarchical multi-agent systems. Describe
> orchestrators, agents, prompts, resolvers, tools, MCP servers, runtime hooks,
> and graph topology in YAML; the engine validates, compiles, and runs that
> system through a long-lived runtime.

**Status: 🚧 Active development.** The YAML validator, compiler, LangGraph
runtime, prompt rendering, resolver/tool plugins, remote **MCP** tools (with
optional tool tags), **runtime hooks** (incl. `before_mcp_request` auth-header
injection), structured logging, the HTTP API (`serve`), and the CLI
(`validate`, `inspect`, `generate`, `run`, `serve`, `chat`) are **implemented**.
A Docker image is provided; richer observability is still planned.

---

## 1. What this is

A platform that turns a declarative YAML description into a running,
traceable, multi-agent application. You declare *what* exists (MCP servers,
tools, resolvers, hooks, orchestrators, agents, prompts) and *how it is
connected* (`graph` indentation), and the engine handles validation,
compilation, routing, prompt rendering, plugin calls, MCP access, lifecycle
hooks, and execution.

## 2. Why it exists

Building multi-agent systems by hand means re-implementing the same plumbing
every time: prompt rendering, routing between agents, tool wiring, permission
enforcement, auth/context resolution, and tracing. This project moves that
plumbing into a reusable runtime and lets developers focus on the **declarative
specification** of their system rather than its mechanics.

## 3. Current status

Active development. See the [Roadmap](docs/ROADMAP.md) and [`tasks/`](tasks/) for
per-phase status.

| Area                          | Status |
| ----------------------------- | ------ |
| YAML schema & validation      | ✅ Implemented |
| Compiled agent graph          | ✅ Implemented |
| Runtime engine (LangGraph)    | ✅ Implemented — orchestrators as supervisors, child-as-tool routing |
| Prompt rendering              | ✅ Implemented — file templates, per-request substitution |
| Resolver plugins              | ✅ Implemented — shared/agent-scoped, generated stubs |
| Tool plugins (local)          | ✅ Implemented — Python tools bound per agent |
| Remote MCP tools              | ✅ Implemented — Streamable HTTP discovery via `langchain-mcp-adapters` |
| MCP `tool_tags`               | ✅ Implemented — optional per-server discovery selector |
| Runtime hooks                 | ✅ Implemented — 10 lifecycle points, sync/async, fail-closed |
| MCP auth (`before_mcp_request`) | ✅ Implemented — `HookedMCPAuth` header injection, no token in prompts |
| Plugin `import_roots`         | ✅ Implemented — CWD-independent package imports |
| CLI                           | ✅ `validate`, `inspect`, `generate`, `run`, `serve`, `chat` |
| HTTP API (`serve`)            | ✅ Implemented — `/invoke`, `/stream` |
| Observability                 | ✅ Implemented — pluggable LangChain callbacks; logging backend + **Langfuse** tracing (env-enabled) |
| Docker image                  | ✅ Implemented — `Dockerfile` + `entrypoint.sh`, defaults to `serve` |

## 4. Capabilities at a glance

- **Declarative agents** — orchestrators, agents, prompts, resolvers, tools, and
  graph topology in YAML.
- **LangGraph runtime** — orchestrators run as supervisors; children are exposed
  to the LLM as tools.
- **Local tools** — Python plugins bound only to the agents that declare them.
- **Remote MCP tools** — connect by URL; tools discovered at build time.
- **MCP `tool_tags`** — optionally discover only a server's tagged tool group
  (default header `X-MCP-Tool-Tag`, or an explicit transport override).
- **Runtime hooks** — trusted lifecycle code (auth, policy, audit) run
  automatically; never exposed to the LLM.
- **`HookedMCPAuth`** — `before_mcp_request` hooks inject `Authorization`/HMAC
  headers per request; tokens never reach the model or the logs.
- **Plugin `import_roots`** — make package-path plugin refs importable regardless
  of the working directory.
- **Unified plugin package** — `plugins/` (`hooks/`, `resolvers/`, `tools/`) with
  a single `plugins.toml` manifest.
- **Observability** — pluggable LangChain callback backends injected into the
  engine: a logging trace (always on) and **Langfuse** tracing that self-enables
  when `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` are set.
- **Offline CLI checks** — `validate` and `inspect` without any LLM or network.

## 5. Example YAML shape

Flat sections declare *what exists*; `graph` declares the runtime topology. See
[docs/YAML_SPEC.md](docs/YAML_SPEC.md) for the full specification and
[examples/enterprise-knowledge-assistant/agents.yaml](examples/enterprise-knowledge-assistant/agents.yaml)
for the complete flagship example. A minimal shape:

```yaml
system:
  name: "Knowledge Assistant"

defaults:
  model:
    provider: anthropic
    name: claude-sonnet-4-6

tools:
  build_learning_plan:
    description: "Build a personalized learning roadmap from research findings"

resolvers:
  current_date:
    scope: shared

mcps:
  docs_server:
    url: "https://mcp.example.com/mcp"
    tool_tags: ["reference"]         # optional; sent as X-MCP-Tool-Tag by default

orchestrators:
  research_router:
    description: "Routes the user's request to the right specialist."
    prompts:
      orchestrator: "prompts/research_router/orchestrator.md"

agents:
  documentation_agent:
    description: "Answers questions from official documentation."
    prompts:
      system: "prompts/documentation_agent/system.md"
    resolvers: [current_date]
    tools: [build_learning_plan]
    mcps: [docs_server]

# Optional: trusted lifecycle hooks (auth/policy/audit) — never seen by the LLM.
hooks:
  before_mcp_request:
    - plugin: mcp_auth
      method: before_mcp_request

# Optional: make package-path plugin refs importable from anywhere.
plugins:
  import_roots: ["."]

graph:
  research_router:
    documentation_agent:
```

### Flagship example: Enterprise Knowledge Assistant

[`examples/enterprise-knowledge-assistant/`](examples/enterprise-knowledge-assistant/)
is the reference system for this project — the example to read first to
understand what EXTRA actually builds. It is a multi-agent research and
documentation assistant with:

- a **root orchestrator** (`research_router`) that routes into two
  **nested sub-orchestrators** (`knowledge_router`, `analysis_router`);
- **two remote MCP servers** — a public one (DeepWiki) and an **authenticated**
  one (Context7), whose credentials are injected per request by a
  `before_mcp_request` hook, never stored in YAML;
- a **protected agent** (`enterprise_docs_agent`) gated by the access plugin;
- **local business tools** (`generate_decision_matrix`, `build_learning_plan`)
  whose implementation is left to the application developer;
- **shared and agent-scoped resolvers**, a **per-node model override**, and
  five **runtime hooks** (startup validation, MCP auth, tool-call auditing,
  tool-result truncation, and run-error recording).

The YAML declares the system; only the plugin code under
[`examples/enterprise-knowledge-assistant/plugins/`](examples/enterprise-knowledge-assistant/plugins/)
is user-implemented. Everything else — validation, compilation, routing,
prompt rendering, and execution — is handled by the engine.

```bash
# Offline — no network, no LLM calls, no secrets required
agentctl validate examples/enterprise-knowledge-assistant/agents.yaml
agentctl inspect  examples/enterprise-knowledge-assistant/agents.yaml

# Live — copy .env.example to .env and fill in your keys first
cp examples/enterprise-knowledge-assistant/.env.example examples/enterprise-knowledge-assistant/.env
agentctl run --config examples/enterprise-knowledge-assistant/agents.yaml \
  --env examples/enterprise-knowledge-assistant/.env \
  --message "Compare LangGraph and Temporal for building a research agent."
```

## 6. CLI commands

The console script is **`agentctl`** (run `agentctl --help`). A global
`--log-level DEBUG|INFO|WARNING|ERROR` precedes the subcommand.

| Command | Usage | Notes |
|---|---|---|
| `validate` | `agentctl validate <spec.yml>` | Offline pre-flight; exits non-zero on failure |
| `inspect`  | `agentctl inspect <spec.yml>` | Offline summary of agents/MCPs/hooks/plugins/tags |
| `generate` | `agentctl generate --config <spec.yml>` | Create resolver/tool/hook stubs + `plugins.toml` |
| `run`      | `agentctl run --config <spec.yml> --message "..."` | Run one message (`--stream`, `--env`); persists to SQLite via `agent_manager` |
| `serve`    | `agentctl serve --config <spec.yml>` | **Stateless** engine HTTP API — `/invoke`, `/stream`, no persistence, no web client (`--host`, `--port`, default port `8080`, `--env`) |
| `chat`     | `agentctl chat --config <spec.yml>` | Ephemeral developer console; reuse one engine across questions, nothing is persisted (`--stream`, `--env`, `--url`) |

`serve` and `chat` are developer/embedding tools, not the conversation product. For
a persisted, multi-turn HTTP API with SSE streaming and the **official React web
client**, run the separate **`agent-manager`** console script instead (default
port `8100`; see [`docs/WIDGET.md`](docs/WIDGET.md)):

```bash
agent-manager --config examples/enterprise-knowledge-assistant/agents.yaml --port 8100
```

Then open `http://127.0.0.1:8100/demo` to use the official React web client
against your system.

### Engine API vs. Agent Manager API

Two HTTP tiers exist, and the difference matters:

- **Engine API** (`agentctl serve`, `/invoke` + `/stream`) — **stateless**. Each
  request is handled independently by the `agent_engine` package; nothing about
  prior turns is remembered between calls.
- **Agent Manager API** (`agent-manager`, `/conversations` + SSE streaming) —
  **conversation-aware**. Built on top of the Engine API by the `agent_manager`
  package, it persists conversation/message history (SQLite by default) and
  assembles prior turns as context on each new message. This is also what
  serves the official React web client.

`agent_engine` never imports `agent_manager` — the dependency only goes one way,
through the `Engine` / `RunResult` port.

`validate` and `inspect` are **fully offline** — no LLM calls, no MCP network,
no tool execution:

```bash
agentctl validate examples/enterprise-knowledge-assistant/agents.yaml   # schema + import-roots + hooks + prompts
agentctl inspect  examples/enterprise-knowledge-assistant/agents.yaml   # agents, MCP url/tool_tags/transport, hooks
```

`validate` runs the same pre-flight the engine does at build time (parse +
validate, resolve `plugins.import_roots` relative to the spec file, and
import/instantiate declared hooks — hooks are trusted code) but stops before any
network or LLM work. `inspect` never prints secrets: hooks are shown as identity
and failure policy only, and the effective tag transport (default header
`X-MCP-Tool-Tag` vs. an explicit override) is shown per server.

### Interactive chat / simulation

`agentctl chat` is a developer simulation console: it keeps one engine (or one
server connection) alive and lets you ask question after question without
restarting the process. It is **ephemeral** — nothing is written to a
database — unlike `agentctl run`, which persists each conversation turn via
`agent_manager`. Type `exit`, `quit`, or `q` — or press Ctrl-C / Ctrl-D — to
stop. Empty input is ignored, and a single failed question prints the error
and keeps the loop running.

```bash
# Local engine — build the graph once, then ask in a loop (the `run` path reused)
agentctl chat --config examples/enterprise-knowledge-assistant/agents.yaml
agentctl chat --config examples/enterprise-knowledge-assistant/agents.yaml --stream   # token-by-token

# Remote — drive a running `agentctl serve` over its /invoke and /stream HTTP API
agentctl serve --config examples/enterprise-knowledge-assistant/agents.yaml &
agentctl chat --url http://localhost:8080
agentctl chat --url http://localhost:8080 --stream
```

Pass **exactly one** of `--config` (local engine) or `--url` (remote server);
passing both, or neither, is a CLI error. In local mode the engine is built once
and reused across questions — only per-request state lives in each call, and no
auth/user state is stored on the engine. `--env` (local mode) and the global
`--log-level` behave the same as `run`/`serve`; route and tool detail stay in
the logs at your chosen level rather than the console. The session is shown as:

```
You > Which docs cover retries?
Agent > The retry policy is documented in ...
You > exit
```

## 7. Plugins and the extension model

The engine contains **no** client-specific authentication, authorization, or
business-data lookup code. Developers provide Python plugins under a single
package (`plugins/` — `hooks/`, `resolvers/`, `tools/`) described by one manifest,
`plugins/plugins.toml`. See
[examples/enterprise-knowledge-assistant/plugins/](examples/enterprise-knowledge-assistant/plugins/).

- **Resolver plugins** fill prompt variables before a node runs — generated as a
  `SharedResolver` plus per-agent `Resolver` subclasses, loaded by file path.
- **Tool plugins** are Python methods exposed to the LLM at runtime; each agent
  binds only its declared tools.
- **Hook plugins** are trusted lifecycle code run automatically by the runtime
  (auth, policy, audit) — **never** exposed to the LLM. See
  [docs/RUNTIME_HOOKS.md](docs/RUNTIME_HOOKS.md).

`agentctl generate` scaffolds the stubs and creates/updates `plugins.toml`.
Package-path refs (e.g. `plugins.hooks.research_hooks:ResearchHooksHook`) are made
importable via `plugins.import_roots`, resolved relative to the spec file.

## 8. Runtime hooks and MCP auth

**Hooks** run trusted code at fixed lifecycle points — engine start/stop, run
start/end/error, before/after/​error tool calls, and before-MCP-request /
after-MCP-response. They are declared in YAML by explicit `ref` or by managed
`plugin` + `method` (resolved through `plugins.toml`), support sync and async,
and are **fail-closed by default** (`failure_policy: warn` opts out). Hooks are
never advertised to the model and never routed through the tool registry. YAML
registers hooks; hook internals are configured in Python code.

The headline use case is **`before_mcp_request`**: a hook injects the right
`Authorization`/HMAC headers before each MCP HTTP request via `HookedMCPAuth`, so
private MCP servers work without per-server code and **the token never reaches
the LLM or the logs**. Full contract and examples:
[docs/RUNTIME_HOOKS.md](docs/RUNTIME_HOOKS.md).

## 9. MCP servers and tool tags

Declare a remote MCP server by URL; the engine creates one client per server and
discovers its tools during build (see [docs/MCP_AND_TOOLS.md](docs/MCP_AND_TOOLS.md)).
Optionally add a per-server **`tool_tags`** selector so only a tagged tool group
is discovered. By default the tags are sent as the header `X-MCP-Tool-Tag`;
`tool_tag_transport` is an optional advanced override (custom header or query
param). Filtering is **server-side** — only the discovered tools are bound, and
nothing about tags is exposed to the LLM.

The flagship example wires two remote MCP servers — a public one (DeepWiki) and
an authenticated one (Context7) whose credential is injected per request by a
`before_mcp_request` hook. Inspect the wiring offline:

```bash
agentctl inspect examples/enterprise-knowledge-assistant/agents.yaml
```

## 10. How prompts are rendered

Prompt files are **templates**. Parsed templates may be cached, but values are
resolved **per request** from request data and plugin resolvers. Fully-rendered
prompts are **never** cached globally, and missing required variables fail
loudly. Prompt text is **not** a security boundary. See
[docs/PROMPT_RENDERING.md](docs/PROMPT_RENDERING.md).

## 11. Architecture

```text
agent.yml → YAML Loader → Validator → Compiler → CompiledAgentGraph
          → RuntimeEngine (built once) → per-request RunContext
          → resolver context → prompt rendering
          → supervisor execution → tools / MCP (+ hooks) → response + trace
```

**Key rule:** the runtime engine is created **once** at startup; per-request
state lives in a `RunContext`, not on the engine. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## 12. For AI coding agents

This repository is **agent-first**. If you are an AI coding agent:

1. Read [AGENTS.md](AGENTS.md) in full.
2. Read [`.ai/README.md`](.ai/README.md) and the relevant guide in
   [`.ai/skills/`](.ai/skills/).
3. Work strictly within the scope of the current task in [`tasks/`](tasks/).
4. Run `make check` before finishing and report using the format in AGENTS.md.

## 13. Development setup

> Requires **Python 3.11+**. The project uses a `src/` layout with packages
> `agent_engine` (the engine) and `agentctl` (the CLI), configured via
> `pyproject.toml` (hatchling build; `ruff`, `mypy`, `pytest`).

```bash
python3 -m venv .venv && source .venv/bin/activate   # create + activate venv
make install                                         # pip install -e ".[dev]"

agentctl --help                                      # console script
agentctl validate examples/enterprise-knowledge-assistant/agents.yaml   # offline spec check
make check                                           # lint + typecheck + test
```

### Everyday commands

```bash
make format      # auto-format (ruff format)
make lint        # lint (ruff check)
make typecheck   # type-check (mypy)
make test        # run tests (pytest)
make check       # lint + typecheck + test (run before finishing a task)
make validate    # offline: validate the flagship example
make inspect     # offline: summarize the flagship example (agents/MCPs/hooks)
```

Leave the environment with `deactivate`. For a clean rebuild:
`rm -rf .venv && python3 -m venv .venv && source .venv/bin/activate && make install`.

In PyCharm/VS Code, point the interpreter at `<repo>/.venv/bin/python` and mark
`src/` as a *Sources Root* so the editable install resolves.

## 14. Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for the phased plan. Foundation → spec &
validation → compiler → runtime → prompts → plugin context → tools/MCP →
hooks → CLI/API → observability (logging + Langfuse) → Docker image are
implemented; additional tracing backends are planned.

## License

To be determined. This is a new open-source project; a license will be added
before any public release.
