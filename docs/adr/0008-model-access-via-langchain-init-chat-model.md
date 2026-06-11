# ADR 0008: Model access goes through LangChain `init_chat_model`, driven by `ModelSpec`

## Status

Accepted

## Context

Every node (orchestrator or agent) may declare its own model, and the user
writes that model in YAML — `model: {provider, name, temperature}`
(`ModelSpec`). The compiler already resolves an effective `ModelSpec` per node
(default inheritance + full-replacement override; see ADR 0006). The runtime
must turn that data into a callable chat model **without coupling to any one
provider** — the user may pick Anthropic, OpenAI, or others.

We already adopted LangGraph as the execution engine. LangGraph is part of the
LangChain ecosystem, which ships:

- `init_chat_model("<provider>:<name>", ...)` — a provider-agnostic factory that
  lazily imports the relevant integration package.
- `BaseChatModel` — a uniform model interface (`.invoke(messages)` etc.).
- Fake chat models (`GenericFakeChatModel`) for deterministic, offline tests.

## Decision

Model access goes through LangChain's `init_chat_model`, wrapped by a single
factory function `build_chat_model(spec: ModelSpec) -> BaseChatModel`. The
runtime depends only on `BaseChatModel`, never on a provider SDK.

- The provider is selected at runtime from `spec.provider` (the YAML value);
  changing provider is a YAML change, not a code change.
- Provider integration packages (`langchain-anthropic`, `langchain-openai`, …)
  are optional installs; `init_chat_model` imports only the one named.
- A model is built **per node** from that node's resolved `ModelSpec`, once at
  graph-build time (consistent with ADR 0001 — built once, not per request).
- We do **not** introduce our own model Protocol on top of `BaseChatModel`.
  Adding an abstraction over an already-provider-agnostic interface, while we
  are committed to the LangChain ecosystem, would be speculative.

## Consequences

**Positive**
- No coupling to a single provider or model; per-node models are supported
  directly from the spec.
- We reuse a maintained abstraction instead of writing/owning provider adapters.
- Tests inject `GenericFakeChatModel` and never touch the network.

**Negative / constraints**
- We are limited to providers LangChain integrates (acceptable — we already
  depend on the ecosystem).
- `build_chat_model` is the single chokepoint that touches LangChain's model
  layer; if we ever leave LangChain, only that function and the node-model
  wiring change.

## Alternatives considered

- **Custom `ChatModel` Protocol + per-provider adapters.** Rejected as
  speculative: it re-abstracts `BaseChatModel`, which is already
  provider-agnostic, and duplicates `init_chat_model`'s dispatch.
- **Import a provider SDK (e.g. `anthropic`) directly in the runtime.** Rejected
  — couples the runtime to one provider and breaks user-chosen models.

## Related

- [ADR 0001 — RuntimeEngine created once](0001-runtime-engine-created-once.md)
- [ADR 0006 — Reusable node declarations and agent nodes](0006-reusable-agent-definitions-and-hierarchy-instances.md)
