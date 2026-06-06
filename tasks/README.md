# Tasks

Small, ordered implementation units. Work them **in numeric order** unless told
otherwise — later tasks depend on earlier ones. Each task is intentionally
narrow; do only what its scope says and propose new tasks for discovered work.

Every task file has: **Goal, Context, Scope, Files allowed to change,
Requirements, Out of scope, Acceptance criteria, Commands to run before
finishing, Expected final report.**

| #    | Task                                                       | Depends on |
| ---- | ---------------------------------------------------------- | ---------- |
| 0001 | [Repository foundation](0001-repository-foundation.md)     | —          |
| 0002 | [YAML schema & validation](0002-yaml-schema-and-validation.md) | 0001   |
| 0003 | [Compiled agent graph](0003-compiled-agent-graph.md)       | 0002       |
| 0004 | [Runtime engine](0004-runtime-engine.md)                   | 0003       |
| 0005 | [Prompt rendering](0005-prompt-rendering.md)               | 0004       |
| 0006 | [Plugin context/access](0006-sidecar-auth-context.md)      | 0004, 0005 |
| 0007 | [MCP tools & plugin tools](0007-mcp-tools-and-permissions.md) | 0004, 0006 |
| 0008 | [CLI](0008-cli.md)                                          | 0002–0004  |
| 0009 | [API server](0009-api-server.md)                           | 0004       |
| 0010 | [Docker deployment](0010-docker-deployment.md)             | 0009       |
| 0011 | [Observability & tracing](0011-observability-and-tracing.md) | 0004     |
| 0012 | [Tests & quality gates](0012-tests-and-quality-gates.md)   | all        |

Before starting any task, read `AGENTS.md` and the skill(s) the task lists.
