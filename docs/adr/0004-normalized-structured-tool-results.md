# ADR 0004 — Preserve structured tool results separately from model text

- **Status:** Accepted
- **Date:** 2026-09-01

## Context

The tool execution path reduced every successful local or MCP result to a
string. Text extraction kept MCP content blocks readable for the model, but it
discarded MCP `structuredContent`, LangChain artifact metadata, and structured
local-tool returns. The idempotency ledger consequently replayed only text, and
`transform_tool_result` hooks could not inspect machine-readable output.

## Decision

Extra owns a provider-agnostic frozen `NormalizedToolResult` with independent
`text`, `structured`, and `artifact` fields.

- The LangChain tool adapter is the normalization boundary. It extracts text
  from content blocks, reads MCP structured output from the current
  `ToolMessage.artifact.structured_content` contract (and the compatible
  `structuredContent` spelling), and removes LangChain-specific objects before
  the result enters the runtime.
- Local JSON object/array results that LangChain serializes into
  `ToolMessage.content` are parsed back into `structured` while the serialized
  model-facing text remains unchanged.
- The model loop consumes only `NormalizedToolResult.text`. It creates a plain
  `ToolMessage` without an artifact, so structured values do not enter model
  context, LangChain callbacks, or tracing as hidden message metadata.
- A structured-only result receives deterministic canonical JSON as its model
  text. A completely empty result receives a stable placeholder. Unsupported
  values fail with controlled model text; provider objects are never rendered
  through `repr()` or arbitrary `str()` fallback. The structured-only JSON is
  ordinary model text and therefore has the same model/callback visibility as
  any other tool text.
- `ToolResultContext.result` remains the text field for hook compatibility and
  gains additive `structured_result` and `artifact` fields. `with_result()`
  changes text only, so truncating text cannot discard structured output.
- The value object validates JSON-like values, stores canonical JSON privately,
  and returns copies from its accessors. Provider-owned nested objects therefore
  cannot mutate the authoritative result after normalization.
- The tool-execution repository persists a versioned JSON-primitive payload.
  An atomic claim identifies one execution owner; concurrent duplicates wait
  for a terminal result and replay it without re-invoking the provider. Terminal
  rows cannot be overwritten, and legacy string records remain readable as
  text-only results.
- Artifact mappings retain structurally safe, bounded metadata under explicit
  per-value, aggregate-size, collection-count, key-length, and nesting limits.
  Binary and oversized values are replaced by type/size/omission metadata and
  are never copied into model context. This is structural safety, not semantic
  secret classification; trusted hooks remain responsible for application-level
  redaction before persistence when needed.
- Structured values are not validated against application output schemas in
  v1, but they must satisfy the generic JSON-safe runtime contract. Malformed
  provider results become controlled failed tool results.

## Contract changes

- `ToolExecutionRepository.complete()` accepts the versioned persisted payload,
  and the port adds `wait_for_completion()`. Custom repository adapters must
  serialize all three fields, atomically create claims, reject terminal
  overwrites, and wake duplicate waiters after every owner outcome.
- `ToolResultContext` adds optional `structured_result` and `artifact` fields
  plus immutable update helpers. Existing text-only hooks remain source
  compatible.
- `ToolInvoker.invoke()` returns `NormalizedToolResult`; this is an internal
  engine contract consumed by the model loop.

There is no YAML, HTTP API, widget, or model-facing text contract change.

## Consequences

- Text-only local tools behave exactly as before.
- MCP text, structured output, and safe artifact metadata can coexist and
  survive idempotent replay.
- Hooks can inspect or deliberately replace structured values without parsing
  text, while ordinary text transforms preserve them by default.
- Structured values are runtime data and are not automatically written to tool
  usage, hidden model-message metadata, logs, traces, or error messages. The
  documented structured-only text fallback is the deliberate model-visible
  exception.
- Existing persisted string results remain readable. The shipped execution
  repository is process-local, so no database migration is required; external
  repository adapters must adopt the expanded port and versioned payload.
- Full multimodal artifact rendering, binary persistence, and output-schema
  enforcement remain out of scope.
