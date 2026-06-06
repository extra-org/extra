# Task 0006 — Plugin Context & Access

## Goal

Implement plugin-based resolver context and protected-node access filtering.
Wire resolver/access plugin seams into the runtime. **No sidecar service in the
current MVP.**

## Context

Customer-specific auth/business logic lives in plugins, not the runtime. The
runtime loads plugin classes, calls fixed methods with `ctx`, maps returned
values, fails closed for protected access, and traces decisions.

**Read first:** `AGENTS.md`, `.ai/skills/sidecar-auth-context.md`,
`docs/SIDECAR_CONTEXT_AUTH.md`, `docs/PROMPT_RENDERING.md`.

## Scope

- Implement plugin reference loading/invocation for resolver methods.
- Implement the fixed `plugins/access.py` contract:
  `AccessResolver.can_access(ctx, node_id) -> bool`.
- Build `ctx` from request headers and request data.
- Wire access filtering before routing protected nodes.
- Map resolver outputs into the request execution context for prompt rendering.

## Files allowed to change

- `src/agentplatform/context/**`
- `src/agentplatform/runtime/**` (only to connect plugin seams)
- `tests/context/**`

## Requirements

- No customer-specific auth/business logic in the runtime.
- Resolver ids referenced by a node call the configured plugin method.
- Protected nodes are hidden from routing unless access returns true.
- `protected: true` without access plugin is a startup/configuration error.
- Access plugin exceptions fail closed and are traced.
- Secrets/tokens are redacted in traces.

## Out of scope

- Tool plugin execution internals (task 0007).
- Real customer plugin implementations beyond fakes/fixtures.
- Sidecar HTTP service support.

## Acceptance criteria

- [ ] Resolver plugin methods can fill prompt variables for a request.
- [ ] Protected nodes are filtered before routing.
- [ ] Access denial/error hides protected nodes and is traced.
- [ ] Missing required access plugin is a clear configuration error.
- [ ] No customer-specific logic is added to the runtime.
- [ ] Tests cover allow/deny/error/missing-plugin cases.
- [ ] `make check` passes.

## Commands to run before finishing

```bash
make check
```

## Expected final report

Use the AGENTS.md §9 format. Confirm customer logic remains in plugins,
protected access fails closed, and task 0007 is recommended next.
