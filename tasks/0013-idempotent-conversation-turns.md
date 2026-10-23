# Task 0013 — Idempotent conversation turn preparation (WA-01)

## Goal and context

Implement the provider-independent preparation prerequisite in the approved
[WhatsApp plan](../docs/WHATSAPP_INTEGRATION_PLAN.md), following
[ADR 0006](../docs/adr/0006-idempotent-conversation-turn-preparation.md).

## Read first

`AGENTS.md`, `.ai/README.md`, project-architecture, architecture-review,
senior-python-engineering, testing, runtime-engine, documentation, and code-review
skills; the current manager service and both persistence adapters.

## Scope / files allowed to change

- This task, `tasks/README.md`, `docs/README.md`, the WhatsApp plan, and ADR 0006.
- New `src/agent_manager/domain/turn_preparation.py`.
- `src/agent_manager/application/conversation_service.py`.
- `src/agent_manager/infrastructure/persistence/{memory_repository,sql_repository,tables}.py`.
- New migration `0007_turn_preparation_receipts.py`.
- New `tests/agent_manager/test_turn_preparation.py` and, if needed, existing
  manager database/repository/service tests.

## Requirements / acceptance criteria

- [x] An opt-in preparation key returns the original message/run/history.
- [x] Conflicting user, conversation, or content is rejected without mutation.
- [x] Receipt, append, head selection, and snapshot commit atomically.
- [x] Concurrent retries prepare one turn; branch conflicts retain no receipt.
- [x] Recovery reconciles missing run registration without resetting a run.
- [x] SQL restart preserves replay; normal callers and budget rules still work.
- [x] Keys are bounded/digested; unsupported capabilities and edits fail clearly.
- [x] Behavior-focused tests and final `make check` pass.

## Out of scope

Executing a recovered turn, webhook/provider APIs, channel queues, account
linking, changes to engine/YAML/HTTP contracts, and production deployment.

## Commands / final report

Run focused tests during development and `make check` before finishing.
Report using `AGENTS.md` section 9; identify remaining WhatsApp stages honestly.

## Implementation result

Completed WA-01 on 2026-10-15. The optional port is implemented by both memory
and SQL adapters; migration 0007 is required before SQL callers opt into keys.
The engine, YAML, and HTTP request shapes were not changed.

Validation: `make check` passed (1,159 tests; 17 existing warnings), including
33 new preparation cases. `git diff --check` and documentation-link checks
also passed. No live Meta, LLM, MCP, or production database was accessed.

WhatsApp transport, durable execution claims/outbox, and account linking are
subsequent tasks. The user confirmed an internal-information pilot, so trusted
organization identity resolution is required before channel activation.
