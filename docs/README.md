# Documentation

This directory contains the design and architecture documentation for the
Declarative Agent Platform. It is the source of truth for **how the system is
meant to work**. The core pipeline (validate → compile → runtime → prompts →
resolver plugins → tool plugins → MCP → CLI) is implemented, with orchestrators
running as supervisor agents. Runtime hooks, execution-limit guardrails, an
Anthropic/Bedrock model layer, basic observability, a Docker image, and two
HTTP API layers — a thin stateless engine API and a conversation-aware
service (`agent_manager`) with the official React web client — are also
implemented; see [ARCHITECTURE.md](ARCHITECTURE.md) §14 for the current
per-layer status. The access plugin is wired into child filtering, but the
request-context gate that should feed it real identity is not yet
implemented, so protected-node access control is not actually enforced today.

## Reading order

1. [ARCHITECTURE.md](ARCHITECTURE.md) — the layered design and request flow.
2. [YAML_SPEC.md](YAML_SPEC.md) — the declarative input format.
3. [RUNTIME_LIFECYCLE.md](RUNTIME_LIFECYCLE.md) — startup vs. per-request lifecycle.
4. [PROMPT_RENDERING.md](PROMPT_RENDERING.md) — how prompts become text.
5. [SIDECAR_CONTEXT_AUTH.md](SIDECAR_CONTEXT_AUTH.md) — client-owned context/auth.
6. [MCP_AND_TOOLS.md](MCP_AND_TOOLS.md) — tools, MCP servers, and permissions.
7. [RUNTIME_HOOKS.md](RUNTIME_HOOKS.md) — trusted lifecycle hooks (auth, policy, audit).
8. [EXECUTION_LIMITS.md](EXECUTION_LIMITS.md) — per-run guardrails against runaway loops.
9. [WIDGET.md](WIDGET.md) — embeddable browser chat widget usage.
10. [WIDGET_ARCHITECTURE.md](WIDGET_ARCHITECTURE.md) — React-in-widget architecture and streaming plan.
11. [CLAUDE_CODE_WORKFLOW.md](CLAUDE_CODE_WORKFLOW.md) — using Claude Code in this repo.
12. [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md) — how to work in this repo.
13. [ROADMAP.md](ROADMAP.md) — phased plan.
14. [adr/](adr/) — architecture decision records for contract-level changes,
    including [immutable conversation branches](adr/0002-conversation-lifecycle-and-immutable-branches.md).

## Integrations in development

- [WhatsApp integration plan](WHATSAPP_INTEGRATION_PLAN.md) — Cloud API
  channel for one organization-owned number, with durable delivery, conversation
  reuse, identity boundaries, and scoped implementation tasks. Generic turn
  preparation, SQL channel storage, authenticated webhook intake, an
  opt-in turn worker with conservative recovery, and opt-in provider delivery
  with text controls are implemented. Customer account linking and live pilot
  packaging remain planned.

[ADR 0006](adr/0006-idempotent-conversation-turn-preparation.md) records the
implemented provider-independent preparation prerequisite for channels.
[ADR 0007](adr/0007-durable-channel-identity-boundary.md) records the implemented
durable channel storage and generic identity port. Subsequent ADRs record the
implemented transport; the customer's Google/JWT account-linking integration
remains pending.
[ADR 0008](adr/0008-authenticated-whatsapp-intake.md) records implemented
authenticated batch intake and commit-before-acknowledgement semantics.
[ADR 0009](adr/0009-channel-worker-ownership-and-recovery.md) records exclusive
worker ownership, fresh identity checks, saved-answer recovery, and shutdown.

[WhatsApp explanation and review](WHATSAPP_REVIEW.md) describes the implemented
path, the shutdown, health, and normalized-input corrections, regression
evidence, and the remaining sending, identity, and pilot work.

[ADR 0010](adr/0010-whatsapp-outbound-delivery.md) records the implemented
WA-05 provider delivery, immutable text plans, authorization/window/suppression
checks and controls in [task 0019](../tasks/0019-whatsapp-delivery-and-controls.md).
The earlier review is a dated snapshot of WA-01–WA-04; its sending follow-up is
now implemented locally. No real account/number has been activated.

[WA-05 delivery review](WHATSAPP_DELIVERY_REVIEW.md) records the subsequent
delivery/control review and cancellation-safe channel client cleanup correction
in [task 0020](../tasks/0020-whatsapp-delivery-review.md).

## Relationship to other directories

- `AGENTS.md` (root) — the operating manual for agents; references these docs.
- `.ai/` — the canonical agent-instruction system (skills, roles, workflows)
  that points back to these docs.
- `tasks/` — small, ordered implementation units that implement this design.
