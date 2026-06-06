# Task 0011 — Observability & Tracing

## Goal

Produce a structured, per-request trace covering routing, access plugin checks,
resolver context, prompt rendering, and tool calls, with secrets redacted, and
expose it for inspection/export.

## Context

Every request should be explainable. The trace accumulator lives on the
`ExecutionContext` (introduced in 0004); this task formalizes its schema, ensures
all layers contribute to it, and adds export.

**Read first:** `AGENTS.md`, `docs/ARCHITECTURE.md` (observability layer),
`docs/RUNTIME_LIFECYCLE.md`, `docs/SIDECAR_CONTEXT_AUTH.md` (redaction).

## Scope

- Define a structured trace schema (events/spans with timing and outcomes).
- Ensure routing, access, resolver context, prompts, and tools emit trace events.
- Provide an export/serialization path (e.g. JSON; optionally OpenTelemetry).

## Files allowed to change

- `src/agentplatform/observability/**`
- Minimal hooks in `runtime`, `context`, `prompts`, `tools` to emit events
- `tests/observability/**`

## Requirements

- Trace is per-request, attached to the `ExecutionContext`.
- Records: routing decisions, access decisions (`allowed`/`reason`),
  resolved context keys (values redacted where sensitive), prompt render events,
  tool calls (final args redacted as needed, allow/deny outcomes).
- **Secrets/tokens are redacted** everywhere in the trace.
- Deterministic, serializable structure; timing included.
- Export does not block or alter request results on failure.

## Out of scope

- Standing up external observability backends/dashboards.
- Changing core runtime/plugin/tool behavior beyond emitting events.

## Acceptance criteria

- [ ] Each request yields a structured, serializable trace.
- [ ] All listed layers contribute trace events.
- [ ] Sensitive values are redacted.
- [ ] Trace export works and is non-blocking on failure.
- [ ] Tests assert trace contents and redaction.
- [ ] `make check` passes.

## Commands to run before finishing

```bash
make check
```

## Expected final report

Use the AGENTS.md §9 format. Confirm redaction and that tracing does not alter
request outcomes. Recommend task 0012 next.
