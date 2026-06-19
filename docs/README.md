# Documentation

This directory contains the design and architecture documentation for the
Declarative Agent Platform. It is the source of truth for **how the system is
meant to work**. The core pipeline (validate → compile → runtime → prompts →
resolver plugins → tool plugins → MCP → CLI) is implemented, with orchestrators
running as supervisor agents. The access plugin is wired into child filtering but
the request-context gate that feeds it is not; the API server, deployment, and
observability layers are documented as design contracts for future
implementation.

## Reading order

1. [ARCHITECTURE.md](ARCHITECTURE.md) — the layered design and request flow.
2. [YAML_SPEC.md](YAML_SPEC.md) — the declarative input format.
3. [RUNTIME_LIFECYCLE.md](RUNTIME_LIFECYCLE.md) — startup vs. per-request lifecycle.
4. [PROMPT_RENDERING.md](PROMPT_RENDERING.md) — how prompts become text.
5. [SIDECAR_CONTEXT_AUTH.md](SIDECAR_CONTEXT_AUTH.md) — client-owned context/auth.
6. [MCP_AND_TOOLS.md](MCP_AND_TOOLS.md) — tools, MCP servers, and permissions.
7. [RUNTIME_HOOKS.md](RUNTIME_HOOKS.md) — trusted lifecycle hooks (auth, policy, audit).
8. [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md) — how to work in this repo.
9. [ROADMAP.md](ROADMAP.md) — phased plan.

## Architecture Decision Records

The [`adr/`](adr/) directory records the binding decisions that shape the
codebase. Read them before changing anything they cover:

- [0001 — RuntimeEngine created once](adr/0001-runtime-engine-created-once.md)
- [0002 — YAML is compiled, not executed directly](adr/0002-yaml-is-compiled-not-executed-directly.md)
- [0003 — Client-specific logic lives in plugins](adr/0003-client-specific-logic-lives-in-sidecar.md)
- [0004 — Prompts are templates rendered per request](adr/0004-prompts-are-templates-rendered-per-request.md)
- [0005 — Prompt templates are rendered per request using resolved context](adr/0005-prompt-rendering-and-context-resolution.md)
- [0006 — Reusable node declarations and agent nodes](adr/0006-reusable-agent-definitions-and-hierarchy-instances.md)
- [0007 — Build/compile phase is separate from the runtime/execution phase](adr/0007-build-phase-separate-from-runtime-phase.md)
- [0008 — Model access via init_chat_model](adr/0008-model-access-via-langchain-init-chat-model.md)
- [0009 — Orchestrators are supervisor agents (children exposed as tools)](adr/0009-orchestrators-are-supervisor-agents.md)
- [0010 — Runtime hooks are a separate concept from tools](adr/0010-runtime-hooks.md)

## Relationship to other directories

- `AGENTS.md` (root) — the operating manual for agents; references these docs.
- `.ai/` — the canonical agent-instruction system (skills, roles, workflows)
  that points back to these docs.
- `tasks/` — small, ordered implementation units that implement this design.
