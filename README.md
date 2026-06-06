# Agent Engine

> A declarative Python engine for hierarchical multi-agent systems. Describe
> orchestrators, agents, prompts, resolvers, tools, MCP servers, and graph
> topology in YAML; the engine validates, compiles, and runs that system through
> a long-lived runtime.

**Status: 🚧 Repository foundation phase.** This repository currently contains
documentation, architecture decisions, agent skills, and ordered implementation
tasks. **The runtime, YAML parser, compiler, plugin loader, MCP client, and API
are not implemented yet.** Nothing below describing the runtime is working
software — it describes the intended design that future work will build.

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

- A single YAML file fully describes an agent system.
- The specification is **validated** and **compiled** into a typed agent graph.
- A **long-lived runtime** executes requests against that graph.
- **Prompts are files** rendered per request from resolver values.
- **Customer-specific logic** lives in customer Python plugins, not in the
  generic engine.
- The engine is **stateless with respect to conversation**: callers send a
  complete conversation each invocation.
- Every request produces a **trace** for observability and debugging.

## 4. Current status

Repository foundation phase. See the [Roadmap](docs/ROADMAP.md) and the
[`tasks/`](tasks/) directory for the planned, ordered implementation work.

| Area                      | Status         |
| ------------------------- | -------------- |
| Documentation & ADRs      | ✅ In place     |
| Agent skills              | ✅ In place     |
| Implementation tasks      | ✅ Defined      |
| YAML schema & validation  | ⏳ Planned (0002) |
| Compiled agent graph      | ⏳ Planned (0003) |
| Runtime engine            | ⏳ Planned (0004) |
| Prompt rendering          | ⏳ Planned (0005) |
| Plugin context/access     | ⏳ Planned (0006) |
| Tools & MCP               | ⏳ Planned (0007) |
| CLI / API / Docker        | ⏳ Planned (0008–0010) |
| Observability             | ⏳ Planned (0011) |

## 5. Planned architecture

```
config.yml → validate → compile → CompiledAgentGraph → RuntimeEngine
           → ExecutionContext (per request) → route graph → response + trace
```

Layers: spec → validation → compiler → agent graph → runtime → prompt rendering
→ plugin resolvers/access → MCP/tools → observability → API → CLI → deployment.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Key rule:** `RuntimeEngine` is created **once** at startup; `ExecutionContext`
is created **per request**. See
[ADR 0001](docs/adr/0001-runtime-engine-created-once.md).

## 6. Example YAML shape

This is the **intended** shape (not yet parsed by any code). See
[docs/YAML_SPEC.md](docs/YAML_SPEC.md) for the full specification.

```yaml
system:
  name: "Rami Levy AI System"

defaults:
  model:
    provider: anthropic
    name: claude-sonnet-4-6

tools:
  book_flight:
    class: FlightTools
    method: book_flight

resolvers:
  current_date:
    class: Resolvers
    method: current_date

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
business-data lookup code. Customers provide Python plugins with a uniform
class + method shape. Resolver plugins fill prompt variables before a node runs;
tool plugins are exposed to the LLM at runtime.

Authorization is opt-in. A node can set `protected: true`; if any protected node
exists, the engine expects `plugins/access.py` with
`AccessResolver.can_access(ctx, node_id) -> bool`. Denied protected nodes are
hidden from routers before routing. See
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
> `ruff`, `mypy`, `pytest` for tooling). Only a placeholder CLI (`agentctl version`)
> exists today — product features are not implemented yet.

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
 foundation → spec & validation → compiler → runtime → prompts → plugin access/context →
tools/MCP → CLI/API → deployment → observability → quality gates.

## License

To be determined. This is a new open-source project; a license will be added
before any public release.
