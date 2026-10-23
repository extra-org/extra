# Task 0015 — Authenticated durable WhatsApp intake (WA-03)

## Goal / context

Implement the next stage of the approved WhatsApp plan: an opt-in Cloud API
webhook for one configured account/number and the manager's compiled system.
The user explicitly requested this stage after the WA-03 description.

## Read first

AGENTS.md, .ai/README.md, project-architecture, senior-python-engineering,
testing, architecture-review, documentation, code-review; the WhatsApp plan
and ADR 0008. Verify protocol details against official Meta references.

## Scope / files allowed to change

- This task, tasks/README.md, docs/README.md, docs/WHATSAPP_INTEGRATION_PLAN.md,
  and new docs/adr/0008-authenticated-whatsapp-intake.md.
- New domain/channels/webhooks.py under src/agent_manager/.
- New infrastructure/channels/__init__.py and
  infrastructure/channels/whatsapp/{__init__,config,security,payloads,normalization,webhook}.py.
- New infrastructure/persistence/channels/{intake,webhooks}.py; existing
  channels/repository.py and channels/tables.py, persistence/tables.py, and
  new migration versions/0009_webhook_receipts.py.
- config.py, composition.py, api/app.py, and new api/routes/whatsapp.py.
- New tests/agent_manager/test_whatsapp_webhook.py,
  test_whatsapp_normalization.py, test_webhook_storage.py, test_webhook_migration.py.

All source paths above are relative to src/agent_manager/. Extract the existing
inbox insert transaction helper so webhook batches reuse its deduplication
without copying it or committing individual messages early.

## Requirements / acceptance criteria

- [x] Disabled by default. Explicit settings require distinct secret values,
  configured account/number/connection IDs, and durable SQL. Secrets are hidden
  from settings representations and validation messages.
- [x] GET /channels/whatsapp/webhook validates subscription mode/token and
  returns the exact plain-text challenge, with no host-login dependency.
- [x] POST verifies HMAC-SHA256 over bounded original bytes before parsing or
  writing; malformed/missing/ambiguous signatures fail closed.
- [x] Typed parsing processes all entries/changes/messages for the configured
  account and number; unrelated targets are ignored. Sender IDs stay opaque.
- [x] Text/Unicode is preserved; unsupported media is normalized without
  downloading it. Delivery events are separate from agent input.
- [x] Malformed authenticated items are durably quarantined with safe reason
  codes and positions, preserving valid siblings. No raw payload/error text or
  provider secrets enter receipts, logs, or agent prompts.
- [x] Inbox messages, bindings, normalized delivery events, quarantine metadata,
  and receipt commit atomically before HTTP 200. Failure returns retryable 503;
  duplicate/concurrent callbacks reuse durable receipts and immutable input.
- [x] SQL restart and migration upgrade/downgrade preserve prior channel data.
- [x] App startup wires intake once alongside the existing engine; webhooks
  never create manager conversations, execute agents, or send provider requests.
- [x] Focused tests, make check, git diff --check, and doc links pass.

## Out of scope

Turn/delivery workers, sending or delivery projections, account linking,
Google/JWT integration, support/reset interpretation, media retrieval, live
Meta/account setup, and deployment. New Meta account/BSUID payload contracts
require verified account fixtures before being enabled; do not guess them.

## Commands / final report

Write behavior tests first, run focused tests during implementation, then
make check and git diff --check. Use AGENTS.md section 9's report; distinguish
local verified intake from a live WhatsApp round trip.

## Implementation result

Completed WA-03 on 2026-10-15. The optional endpoints are composed once at
startup. Strict Pydantic boundary models normalize only the configured target;
domain batches/receipts remain provider independent. SQL migration 0009 adds
receipt and delivery-observation tables. The existing inbox insertion was
extracted unchanged in purpose into a transaction helper shared by both SQL
adapters, keeping WA-02 duplicate/conflict behavior intact.

`make check` passed: 1,268 tests including 64 new cases; formatting, lint, and
type checking passed (326 source/test files checked by mypy). The 24 warnings
come from existing dependency-warning categories, including three additional
Alembic configuration warnings exercised by the new migration test.
`git diff --check` and 43 local documentation links also passed.

Tests use real local file-SQLite, a fake engine, and injected storage failures.
They cover signatures and byte tampering, exact challenge responses, bounded
streaming, full batches, malformed sibling isolation, separate status storage,
safe quarantine, concurrent duplicates, failed writes/commit rollback, restart,
and migration upgrade/downgrade. Startup builds the engine once and callbacks
create no manager conversations or agent runs.

Self-review corrected malformed-collection coupling (valid messages and status
siblings now survive independently), missing-entry quarantine, excessive
content-length conversion, and unpersistable startup system IDs, with focused
regression cases. No blocking review findings remain within this scope.

No real account, secrets, webhook subscription, provider sends, model calls,
MCP calls, or production database were used. PostgreSQL is supported by the
adapter but was not verified against a live server. Current account-model/BSUID
variants remain explicit compatibility work before deployment. Quarantine
retains safe metadata rather than raw bodies and cannot replay raw payloads.

Next: WA-04 turn worker and recovery, including fresh organization identity
checks before execution and reconciliation of ambiguous runs. Google/JWT
account linking and outgoing delivery remain separate subsequent tasks.
