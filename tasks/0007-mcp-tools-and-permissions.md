# Task 0007 — MCP Tools & Plugin Tools

## Goal

Implement the tool registry, Python plugin tool invocation, and MCP server
integration. Wire them into the runtime's tool seam.

## Context

Tools are LLM-invoked runtime capabilities. Resolver plugins run before a node;
tool plugins are exposed to executor agents during execution. MCP servers provide
external tools and may be implemented in any language.

**Read first:** `AGENTS.md`, `.ai/skills/mcp-tools.md`,
`docs/MCP_AND_TOOLS.md`, `docs/SIDECAR_CONTEXT_AUTH.md`.

## Scope

- Implement a tool registry built at startup from the compiled graph.
- Load Python plugin tool classes once and invoke configured methods.
- Integrate MCP servers from `mcps` declarations.
- Connect tool execution to the runtime seam and trace tool calls.

## Files allowed to change

- `src/agentplatform/tools/**`
- `src/agentplatform/runtime/**` (only to connect the tool seam)
- `tests/tools/**`

## Requirements

- Every agent tool id resolves to a declared tool plugin reference.
- Tool plugin methods receive `ctx` and model-proposed arguments.
- MCP connections/clients are created once and reused; per-request data flows via
  `ExecutionContext`.
- Tool calls and MCP calls are traced with secrets redacted.
- No secrets in YAML.

## Out of scope

- Per-tool permissions/input policies unless the schema is extended first.
- API/CLI surfaces (0008, 0009).
- Deployment (0010), deep observability beyond basic tool-call tracing (0011).

## Acceptance criteria

- [ ] Tool plugin references load and invoke through the registry.
- [ ] MCP server declarations are resolved and clients are shared.
- [ ] Per-request context is passed to tools without shared request state.
- [ ] Tool/MCP calls are traced with redaction.
- [ ] Tests cover plugin tool success/failure and MCP binding.
- [ ] `make check` passes.

## Commands to run before finishing

```bash
make check
```

## Expected final report

Use the AGENTS.md §9 format. Confirm tools are runtime capabilities, resolvers
remain pre-node context, and task 0008 is recommended next.
