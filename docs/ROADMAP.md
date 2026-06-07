# Roadmap

A phased, honest plan. The repository is in the **foundation phase**. Each phase
below maps to one or more task files in `tasks/`. Status reflects reality, not
aspiration.

| Phase | Theme                         | Tasks      | Status        |
| ----- | ----------------------------- | ---------- | ------------- |
| 0     | Repository foundation         | docs/.ai/tasks | ✅ done      |
| 1     | Package skeleton & tooling    | 0001       | ✅ done        |
| 2     | YAML schema & validation      | 0002       | ✅ done        |
| 3     | Compiled agent graph          | 0003       | ⏳ planned     |
| 4     | Runtime engine                | 0004       | ⏳ planned     |
| 5     | Prompt rendering              | 0005       | ⏳ planned     |
| 6     | Plugin context/access         | 0006       | ⏳ planned     |
| 7     | MCP & plugin tools            | 0007       | ⏳ planned     |
| 8     | CLI                           | 0008       | ⏳ planned     |
| 9     | API server                    | 0009       | ⏳ planned     |
| 10    | Docker / deployment           | 0010       | ⏳ planned     |
| 11    | Observability & tracing       | 0011       | ⏳ planned     |
| 12    | Tests & quality gates         | 0012       | ⏳ planned     |

## Open-source developer-experience milestones

The platform is **self-hosted open source**. The first experience must be simple
and run with **mocks only** (mock LLM provider, mock tool/MCP layer, simple YAML
and prompts, no real external services). The bundled `examples/agents.yml` will
demonstrate the product end-to-end. The CLI (working name **`agentctl`**) unlocks
these milestones in order:

| Milestone | Command (on `examples/agents.yml`) | Enabled by |
| --------- | ---------------------------------- | ---------- |
| Validate  | `agentctl validate examples/agents.yml` | ✅ 0002 |
| Inspect   | `agentctl graph examples/agents.yml` | 0003 |
| Run local | `agentctl run examples/agents.yml --message "hello"` | 0004–0005 (mock LLM/tools) |
| Serve     | `agentctl serve examples/agents.yml` | 0009 |

Real LLMs, real MCP, real customer plugins, and deployment come **after** the mock-based
local experience works.

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
- Client-specific auth or business logic (this is the customer's plugin's job).
- Turning YAML into a general-purpose programming language.
