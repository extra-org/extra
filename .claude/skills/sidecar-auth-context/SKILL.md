---
name: sidecar-auth-context
description: Use when working on the sidecar client/contract or context/auth resolution. Keeps client-specific logic out of the runtime and defaults to deny.
---

# Skill: Sidecar Auth & Context

## Purpose

Resolve client-specific identity/context/permissions through a client-owned
sidecar via a standard contract; the runtime calls, maps, and enforces. Primary
task: `0006`.

## When to Use

- Working on the sidecar client, the `/resolve-context` contract, or mapping its
  response into `ExecutionContext`.

## Files to Read First

- `skills/sidecar-auth-context-skill.md` (root playbook).
- `docs/SIDECAR_CONTEXT_AUTH.md`,
  `docs/adr/0003-client-specific-logic-lives-in-sidecar.md`.

## Rules

- Client-specific auth/authorization/business context lives in the sidecar (or a
  plugin boundary), never in the runtime.
- The runtime calls the sidecar through a standard, generic contract.
- The sidecar resolves identity, permissions, dynamic context, and tool
  policies; the runtime maps the result onto `ExecutionContext` and enforces it.
- Support `pre_routing` and `pre_agent` phases.
- Failure policy defaults to **deny** (fail closed); `allowed: false` blocks and
  is traced; secrets redacted in traces.

## Process

1. Define contract request/response types from the doc.
2. Build requests from agent-declared `required_context`/`required_permissions`.
3. Map `identity`/`permissions`/`context`/`tool_policy` onto `ExecutionContext`.
4. Enforce `allowed` and fail-closed; trace decisions.
5. Test allow/deny/error with a fake sidecar; `make check`.

## Checklist Before Finishing

- [ ] No client-specific logic in the runtime; contract stays generic.
- [ ] Response mapped onto `ExecutionContext`; `allowed:false` blocks + traces.
- [ ] Sidecar errors fail closed; injected policy values non-overridable.
- [ ] Secrets redacted; fake-sidecar tests; `make check` passes.

## Common Mistakes to Avoid

- Implementing auth/tenant/business rules in the runtime.
- Failing open on sidecar errors; logging raw tokens.

## Expected Final Report

Confirm no client logic in the runtime, the contract/phases supported,
fail-closed behavior, redaction, fake-sidecar test coverage, and `make check`.
