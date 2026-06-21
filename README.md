# Agent Engine

> A declarative Python engine for hierarchical multi-agent systems. Describe
> orchestrators, agents, prompts, resolvers, tools, MCP servers, runtime hooks,
> and graph topology in YAML; the engine validates, compiles, and runs that
> system through a long-lived runtime.

**Status: 🚧 Active development.** The YAML validator, compiler, LangGraph
runtime, prompt rendering, resolver/tool plugins, remote **MCP** tools (with
optional tool tags), **runtime hooks** (incl. `before_mcp_request` auth-header
injection), structured logging, the HTTP API (`serve`), and the CLI
(`validate`, `inspect`, `generate`, `run`, `serve`) are **implemented**.
Deployment/Docker and richer observability are still planned.

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
| Access plugin                 | 🔶 Partial — `protected` child filtering wired; request-context gate minimal |
| CLI                           | ✅ `validate`, `inspect`, `generate`, `run`, `serve` |
| HTTP API (`serve`)            | ✅ Implemented — `/invoke`, `/stream` |
| Observability                 | ✅ Implemented — pluggable LangChain callbacks; logging backend + **Langfuse** tracing (env-enabled) |
| Deployment / Docker           | ⏳ Planned |

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
- **Local example MCP server** — a runnable server for end-to-end smoke testing
  of discovery, tags, and auth headers (see
  [examples/local_mcp_server/](examples/local_mcp_server/)).
- **Observability** — pluggable LangChain callback backends injected into the
  engine: a logging trace (always on) and **Langfuse** tracing that self-enables
  when `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` are set.
- **Offline CLI checks** — `validate` and `inspect` without any LLM or network.

## 5. Example YAML shape

Flat sections declare *what exists*; `graph` declares the runtime topology. See
[docs/YAML_SPEC.md](docs/YAML_SPEC.md) for the full specification and
[examples/agents.yml](examples/agents.yml).

```yaml
system:
  name: "Rami Levy AI System"

defaults:
  model:
    provider: anthropic
    name: claude-sonnet-4-6

tools:
  book_flight:
    description: "Search and book a flight given origin, destination and travel date"

resolvers:
  current_date:
    scope: shared

mcps:
  businesscenter:
    url: "https://mcp.company.com/mcp"
    tool_tags: ["invoices"]          # optional; sent as X-MCP-Tool-Tag by default

orchestrators:
  main_router:
    description: "Routes the user by topic."
    prompts:
      orchestrator: "prompts/main_router/orchestrator.md"

agents:
  domestic_flights_agent:
    description: "Search and book flights within the country."
    prompts:
      system: "prompts/domestic_flights/system.md"
    resolvers: [current_date]
    tools: [book_flight]
    mcps: [businesscenter]

# Optional: trusted lifecycle hooks (auth/policy/audit) — never seen by the LLM.
hooks:
  before_mcp_request:
    - plugin: mcp_auth
      method: before_mcp_request
      config: { credential_env: INTERNAL_DOCS_CREDENTIAL }

# Optional: make package-path plugin refs importable from anywhere.
plugins:
  import_roots: ["."]

graph:
  main_router:
    domestic_flights_agent:
```

## 6. CLI commands

The console script is **`agentctl`** (run `agentctl --help`). A global
`--log-level DEBUG|INFO|WARNING|ERROR` precedes the subcommand.

| Command | Usage | Notes |
|---|---|---|
| `validate` | `agentctl validate <spec.yml>` | Offline pre-flight; exits non-zero on failure |
| `inspect`  | `agentctl inspect <spec.yml>` | Offline summary of agents/MCPs/hooks/plugins/tags |
| `generate` | `agentctl generate --config <spec.yml>` | Create resolver/tool/hook stubs + `plugins.toml` |
| `run`      | `agentctl run --config <spec.yml> --message "..."` | Run one message (`--stream`, `--env`) |
| `serve`    | `agentctl serve --config <spec.yml>` | HTTP API (`--host`, `--port`, `--env`) |

`validate` and `inspect` are **fully offline** — no LLM calls, no MCP network,
no tool execution:

```bash
agentctl validate examples/local_mcp_agent_invoices.yml   # schema + import-roots + hooks + prompts
agentctl inspect  examples/local_mcp_agent_invoices.yml   # agents, MCP url/tool_tags/transport, hooks
```

`validate` runs the same pre-flight the engine does at build time (parse +
validate, resolve `plugins.import_roots` relative to the spec file, and
import/instantiate declared hooks — hooks are trusted code) but stops before any
network or LLM work. `inspect` never prints secrets: hook config is shown as
`config_keys: [...]` only, and the effective tag transport (default header
`X-MCP-Tool-Tag` vs. an explicit override) is shown per server.

## 7. Plugins and the extension model

The engine contains **no** customer-specific authentication, authorization, or
business-data lookup code. Customers provide Python plugins under a single
package (`plugins/` — `hooks/`, `resolvers/`, `tools/`) described by one manifest,
`plugins/plugins.toml`. See [examples/plugins/](examples/plugins/).

- **Resolver plugins** fill prompt variables before a node runs — generated as a
  `SharedResolver` plus per-agent `Resolver` subclasses, loaded by file path.
- **Tool plugins** are Python methods exposed to the LLM at runtime; each agent
  binds only its declared tools.
- **Hook plugins** are trusted lifecycle code run automatically by the runtime
  (auth, policy, audit) — **never** exposed to the LLM. See
  [docs/RUNTIME_HOOKS.md](docs/RUNTIME_HOOKS.md).
- **Access plugin** — a node may set `protected: true`; if any protected node
  exists, the engine filters those children through
  `plugins/access.py::AccessResolver.can_access(ctx, node_id)`. Child filtering
  is wired; the request-context gate that populates `ctx` is still minimal. See
  [docs/SIDECAR_CONTEXT_AUTH.md](docs/SIDECAR_CONTEXT_AUTH.md).

`agentctl generate` scaffolds the stubs and creates/updates `plugins.toml`.
Package-path refs (e.g. `examples.plugins.hooks.mcp_auth:McpAuthHook`) are made
importable via `plugins.import_roots`, resolved relative to the spec file.

## 8. Runtime hooks and MCP auth

**Hooks** run trusted code at fixed lifecycle points — engine start/stop, run
start/end/error, before/after/​error tool calls, and before-MCP-request /
after-MCP-response. They are declared in YAML by explicit `ref` or by managed
`plugin` + `method` (resolved through `plugins.toml`), support sync and async,
and are **fail-closed by default** (`failure_policy: warn` opts out). Hooks are
never advertised to the model, never routed through the tool registry, and hook
config values are never logged.

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

To try all of this end-to-end without a public server, run the bundled local
demo MCP server:

```bash
# terminal 1
python -m examples.local_mcp_server.server         # Streamable HTTP on :8765/mcp
# terminal 2
agentctl run --config examples/local_mcp_agent_invoices.yml --message "List the invoices"
```

See [examples/local_mcp_server/README.md](examples/local_mcp_server/README.md).

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
          → access filtering → resolver context → prompt rendering
          → supervisor execution → tools / MCP (+ hooks) → response + trace
```

**Key rule:** the runtime engine is created **once** at startup; per-request
state lives in a `RunContext`, not on the engine. See
[ADR 0001](docs/adr/0001-runtime-engine-created-once.md) and
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
agentctl validate examples/agents.yml                # offline spec check
make check                                           # lint + typecheck + test
```

### Everyday commands

```bash
make format      # auto-format (ruff format)
make lint        # lint (ruff check)
make typecheck   # type-check (mypy)
make test        # run tests (pytest)
make check       # lint + typecheck + test (run before finishing a task)
```

Leave the environment with `deactivate`. For a clean rebuild:
`rm -rf .venv && python3 -m venv .venv && source .venv/bin/activate && make install`.

In PyCharm/VS Code, point the interpreter at `<repo>/.venv/bin/python` and mark
`src/` as a *Sources Root* so the editable install resolves.

## 14. Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for the phased plan. Foundation → spec &
validation → compiler → runtime → prompts → plugin access/context → tools/MCP →
hooks → CLI/API → observability (logging + Langfuse) are implemented;
deployment/Docker and additional tracing backends are planned.

## License

To be determined. This is a new open-source project; a license will be added
before any public release.
