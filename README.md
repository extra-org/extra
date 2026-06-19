# Agent Engine

> A declarative Python engine for hierarchical multi-agent systems. Describe
> orchestrators, agents, prompts, resolvers, tools, MCP servers, and graph
> topology in YAML; the engine validates, compiles, and runs that system through
> a long-lived runtime.

**Status: 🚧 Active development.** The YAML validator, compiler, runtime
engine, LangGraph-based routing, resolver plugin system, tool plugin loading,
prompt file rendering, and CLI (`validate`, `generate`, `run`) are
**implemented**. The access plugin, MCP client, API server, deployment, and
observability are **not yet implemented**.

---

## 1. What this is

A platform that turns a declarative YAML description into a running,
traceable, multi-agent application. You declare *what* exists (MCP servers,
tools, resolvers, orchestrators, agents, prompts) and *how it is connected*
(`graph` indentation), and the engine handles validation, compilation, routing,
prompt rendering, plugin calls, MCP access, and execution.

## 2. Why it exists

Building multi-agent systems by hand means re-implementing the same plumbing
every time: prompt rendering, routing between agents, tool wiring, permission
enforcement, auth/context resolution, and tracing. This project moves that
plumbing into a reusable runtime and lets developers focus on the **declarative
specification** of their system rather than its mechanics.

## 3. Vision

- A YAML specification describes the desired agent system: what exists and how
  it is connected.
- The specification is **validated** and **compiled** into a typed agent graph.
- A **long-lived runtime** executes requests against that graph.
- **Prompts are files** rendered per request from resolver values.
- **Customer-specific logic** lives in customer plugins, generated resolvers, or
  optional sidecar boundaries, not in the generic engine.
- The engine is **stateless with respect to conversation**: callers send a
  complete conversation each invocation.
- Every request produces a **trace** for observability and debugging.

## 4. Current status

Active development. See the [Roadmap](docs/ROADMAP.md) and the
[`tasks/`](tasks/) directory for per-phase status.

| Area                      | Status         |
| ------------------------- | -------------- |
| Documentation & ADRs      | ✅ In place     |
| Agent skills              | ✅ In place     |
| Implementation tasks      | ✅ Defined      |
| YAML schema & validation  | ✅ Implemented (0002) |
| Compiled agent graph      | ✅ Implemented (0003) |
| Runtime engine            | ✅ Implemented (0004) |
| Prompt rendering          | 🔶 Partial (0005) — file loading + substitution work; no dedicated module |
| Resolver plugins          | ✅ Implemented (0006) — shared/agent-scoped, generation modes, TOML |
| Access plugin             | ⏳ Planned (0006) — contract defined, not wired into routing |
| Tool plugins              | 🔶 Partial (0007) — Python tools work; MCP client not implemented |
| CLI                       | 🔶 Partial (0008) — validate, generate, run, version work |
| API / Docker              | ⏳ Planned (0009–0010) |
| Observability             | ⏳ Planned (0011) |

## 5. Planned architecture

```text
agent.yml → YAML Loader → Validator → Compiler → CompiledAgentGraph
          → RuntimeEngine → ExecutionContext per request
          → Security / Context Gate → Resolver / Sidecar Context Resolution
          → Prompt Rendering → Recursive Agent Execution
          → Tool Permission Enforcement → MCP / Tool Calls
          → Response + Trace
```

Layers: spec → validation → compiler → agent graph → runtime → prompt rendering
→ plugin resolvers/access → MCP/tools → observability → API → CLI → deployment.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Key rule:** `RuntimeEngine` is created **once** at startup; `ExecutionContext`
is created **per request**. See
[ADR 0001](docs/adr/0001-runtime-engine-created-once.md).

## 6. Example YAML shape

This is the supported validation shape. See [docs/YAML_SPEC.md](docs/YAML_SPEC.md)
for the full specification.

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
    return_type: str

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

graph:
  main_router:
    domestic_flights_agent:
```

Flat sections declare *what exists*; `graph` declares the runtime topology.
See [examples/agents.yml](examples/agents.yml) and
[examples/config.schema.json](examples/config.schema.json).

## 7. How plugins and access work

The engine contains **no** customer-specific authentication, authorization, or
business-data lookup code. Customers provide Python plugins.

All customer extension code lives under one plugin package (`plugins/` —
`hooks/`, `resolvers/`, `tools/`) described by a single manifest,
`plugins/plugins.toml` (documentation/generation only; not read at runtime).
See [examples/plugins/](examples/plugins/).

**Resolver plugins** fill prompt variables before a node runs. Resolvers are
generated into a `SharedResolver` (shared methods) plus per-agent `Resolver`
subclasses (agent-specific methods), loaded by file path. The runtime
instantiates the class once and calls methods by name; shared methods resolve
through normal Python inheritance. Run `agentctl generate` to create resolver
stubs — it also creates/updates `plugins/plugins.toml`.

**Tool plugins** are Python methods exposed to the LLM at runtime. Each agent
declares which tools it may use; the runtime binds only those tools.

**Hook plugins** are trusted lifecycle code run automatically by the runtime
(auth, policy, audit) — never exposed to the LLM. See
[docs/RUNTIME_HOOKS.md](docs/RUNTIME_HOOKS.md).

**Access plugin** (planned): a node can set `protected: true`; if any protected
node exists, the engine expects `plugins/access.py` with
`AccessResolver.can_access(ctx, node_id) -> bool`. The contract is defined but
**not yet wired into routing**. See
[docs/SIDECAR_CONTEXT_AUTH.md](docs/SIDECAR_CONTEXT_AUTH.md).

## 8. How prompts are rendered dynamically

Prompt files are **templates**. Parsed templates may be cached, but values are
resolved **per request** from request headers/data and plugin resolvers.
Fully-rendered prompts are
**never** cached globally, and missing required variables fail loudly. Prompt
text is **not** a security boundary. See
[docs/PROMPT_RENDERING.md](docs/PROMPT_RENDERING.md).

## 9. How future agents should work here

This repository is **agent-first**. If you are an AI coding agent:

1. Read [AGENTS.md](AGENTS.md) in full.
2. Read [`.ai/README.md`](.ai/README.md) and the relevant guide in
   [`.ai/skills/`](.ai/skills/).
3. Pick the next task in [`tasks/`](tasks/) and work strictly within its scope.
4. Run `make check` before finishing and report using the format in AGENTS.md.

## 10. Development setup

> Requires **Python 3.11+**. The project uses a `src/` layout with package
> `agentplatform` and is configured via `pyproject.toml` (hatchling build;
> `ruff`, `mypy`, `pytest` for tooling). The CLI supports `agentctl version`,
> `agentctl validate`, `agentctl generate` (with `--mode`, `--agent`,
> `--force`), and `agentctl run`. Graph inspection and serve are planned.

### 1. Create and activate a virtual environment

```bash
python3 -m venv .venv          # create the venv (.venv/ is git-ignored)
source .venv/bin/activate      # macOS/Linux (zsh/bash)
# .venv\Scripts\activate       # Windows (PowerShell: .venv\Scripts\Activate.ps1)
```

### 2. Install the project (editable) with dev dependencies

```bash
make install                   # runs: pip install -e ".[dev]"
```

### 3. Verify

```bash
which python                   # → <repo>/.venv/bin/python
agentctl version               # → 0.0.0  (console script; alias: agent-platform)
agentctl validate examples/agents.yml
agentctl generate examples/agents.yml --mode all
make check                     # lint (ruff) + typecheck (mypy) + test (pytest)
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

### IDE setup (PyCharm / VS Code)

Point your editor's Python interpreter at `<repo>/.venv/bin/python` so it resolves
the editable install. In PyCharm you may also mark `src/` as a *Sources Root*.
This clears any "unresolved reference `agentplatform`" warning (a `src/`-layout
indexing quirk, not a code error).

## 11. Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for the phased plan. In short:
foundation → spec & validation → compiler → runtime → prompts → plugin
access/context → tools/MCP → CLI/API → deployment → observability → quality
gates. Phases 0–4 are done; 5–8 are partially implemented; 9–12 are planned.

## License

To be determined. This is a new open-source project; a license will be added
before any public release.
