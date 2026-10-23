# Task 0014 — Durable channel storage and organization identity (WA-02)

## Goal / context

Implement the next stage of the approved WhatsApp integration plan after task
0013: a provider-independent inbox, sender/conversation bindings, execution
claims, reply staging, and a client-owned organization identity boundary.
The confirmed first deployment needs internal organization information.
The customer identity integration is intentionally deferred; its selected
direction is Google login with a generic JWT-issuing authentication server.
This task implements the mandatory provider-neutral resolver boundary only.

## Read first

`AGENTS.md`, `.ai/README.md`, project-architecture, architecture-review,
senior-python-engineering, sidecar-auth-context, testing, documentation and
code-review skills; the WhatsApp plan and ADRs 0006/0007.

## Scope / files allowed to change

- This task, `tasks/README.md`, `docs/README.md`, `docs/WHATSAPP_INTEGRATION_PLAN.md`,
  and new `docs/adr/0007-durable-channel-identity-boundary.md`.
- New `src/agent_manager/domain/channels/{__init__,models,repository,identity}.py`.
- New `src/agent_manager/application/channels/{__init__,preparation}.py`.
- New `src/agent_manager/infrastructure/persistence/channels/{__init__,tables,repository,replies}.py`.
- `src/agent_manager/infrastructure/persistence/tables.py`, new migration
  `0008_channel_storage.py`, and `src/agent_manager/composition.py`.
- New `tests/agent_manager/test_channel_storage.py`,
  `tests/agent_manager/test_channel_identity.py`, and
  `tests/agent_manager/test_channel_migration.py`.

## Requirements / acceptance criteria

- [x] Typed events, states, ports, and explicit bounded input validation.
- [x] SQL inbox receipt and binding creation are atomic; duplicate provider
  messages reuse one event and conflicting input is rejected without mutation.
- [x] Sender bindings are isolated by connection/system/sender. Ordered claims
  serialize each sender while independent senders can progress concurrently.
- [x] A prepared turn receives one atomic execution claim. Ambiguous/review
  work cannot be silently reclaimed or skipped; no lease expiry reruns it.
- [x] Reset operates only on the current claimed control event, advances the
  binding generation once, and preserves prior conversation history.
- [x] Reply parts commit together, keep stable keys/order, and reject conflicting
  restaging. This task does not send them or claim external delivery.
- [x] A client identity resolver is mandatory for internal preparation. Missing,
  anonymous, revoked, wrong-organization, and changed-owner identities fail
  closed before a new conversation/turn; resolved credentials/roles are never
  persisted in channel rows.
- [x] Current permissions/credentials are resolved for each turn. A crash
  between manager preparation and recording queue IDs reuses WA-01's receipt.
- [x] Migration and file-SQLite restart tests pass. Composition exposes durable
  channel storage only with SQL; process-memory mode has no channel backend.
- [x] `make check` passes; no engine, YAML, HTTP, or provider API changes.

## Out of scope

Meta webhook parsing/signatures, background workers, actual sending/delivery,
automatic replay of interrupted execution, login/account-linking browser UI,
customer-specific identity lookup, live account setup, and production deployment.

## Commands / report

Run focused tests while developing, then `make check` and `git diff --check`.
Report using AGENTS.md section 9, clearly distinguishing this storage/identity
boundary from a live WhatsApp connection.

## Implementation result

Completed WA-02 on 2026-10-15. Migration 0008 adds normalized inbox, sender
bindings, and immutable reply parts. SQL composition exposes the repository;
memory mode exposes none. The mandatory identity port validates a freshly
resolved organization principal before preparing an internal manager turn.

Validation: `make check` passed (1,204 tests, including 45 new channel cases;
21 warnings from existing dependency-warning categories). Formatting, lint,
and type checking passed. Tests cover concurrent intake/preparation/reset/reply
staging, the delayed-reset race, restart, failed-transaction rollback, migration
upgrade/downgrade, revoked/malformed identities, and sanitized error handling.

Self-review found and corrected two concurrency gaps: preparation bookkeeping
now reuses a concurrently committed receipt, and a delayed duplicate reset
cannot clear a newer conversation binding. Each has a regression test. No
blocking review findings remain within this task's scope.

No live Meta, model, MCP, or production database was accessed. PostgreSQL is
supported by the SQL adapter but was not exercised against a live server.
Account linking, Google login/JWT verification, worker reauthorization,
webhooks, and actual sending remain subsequent work. The internal channel
cannot be activated with this storage boundary alone. Next: WA-03, the
disabled-by-default provider/webhook boundary.
