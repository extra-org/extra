# Task 0019 — WhatsApp outbound delivery and controls (WA-05)

## Goal / authorization

Implement the next recommended stage explicitly requested by the user after
the WA-01–WA-04 review: provider sending, ordered durable delivery, current
recipient authorization, retry/uncertain-outcome handling, and text controls.
The internal-information pilot still requires the customer's identity plugin.

## Read first

AGENTS.md, .ai/README.md; project-architecture, senior-python-engineering,
architecture-review, testing, sidecar-auth-context, documentation, code-review;
the WhatsApp plan, ADRs 0006–0010, and existing channel implementation/tests.
Use test-driven development and verification-before-completion. Apply Unicode
roundtrip/bounds properties without introducing a new property-test library.

## Scope / files allowed to change

- This task, tasks/README.md, docs/README.md, docs/WHATSAPP_INTEGRATION_PLAN.md,
  and new docs/adr/0010-whatsapp-outbound-delivery.md.
- pyproject.toml: explicitly declare regex for Unicode grapheme segmentation.
- Under src/agent_manager/: new domain/channels/{delivery,controls}.py,
  application/channels/{delivery,controls}.py,
  infrastructure/channels/whatsapp/{sender,rendering}.py,
  infrastructure/persistence/channels/{delivery,delivery_projection,controls}.py,
  infrastructure/persistence/migrations/versions/0010_channel_delivery.py.
- Existing domain/channels/models.py, application/channels/{worker,processing}.py,
  infrastructure/channels/whatsapp/normalization.py,
  infrastructure/persistence/channels/{tables,intake,repository}.py,
  infrastructure/persistence/tables.py, composition.py, config.py.
- New tests/agent_manager/test_whatsapp_rendering.py, test_whatsapp_sender.py,
  test_channel_delivery.py, test_channel_delivery_storage.py,
  test_channel_controls.py, test_delivery_migration.py,
  test_whatsapp_delivery_lifecycle.py. Existing test_whatsapp_normalization.py,
  test_channel_worker_lifecycle.py only for compatible setup as needed;
  test_webhook_migration.py to seed/read its historical 0008 schema without
  using a current adapter that now requires migration 0010.

Keep modules focused; logical outbox answers remain immutable. New delivery
tables hold provider rendering/attempt state and pinned original ownership.
The existing reset repository also reconciles a concurrently committed reset
receipt if it commits between reading the inbox and sender binding; full
validation exposed this race while extending the same reset behavior.
No engine, YAML, existing HTTP request, or client identity signature change.

## Acceptance criteria

- [x] Sender is off by default; enabling requires the existing worker, explicit
  API version, separate secret token, support contact, and persistent SQL.
- [x] Plain-text rendering preserves exact Unicode/RTL text and grapheme
  clusters; bounded parts prefer paragraph boundaries and never truncate.
- [x] Durable immutable part planning handles existing logical outbox rows;
  CAS claims preserve per-recipient part/turn order across restart.
- [x] Before each attempt, enforce original linked account, organization,
  current opt-out state and a verified inbound 24-hour window.
- [x] One bounded HTTP client is owned by startup. Acceptance/provider IDs are
  persisted separately from delivered/read; callbacks project monotonically,
  including callbacks arriving before the acceptance commit.
- [x] Known rejected/transient attempts use bounded backoff/jitter. Ambiguous
  submission/cancellation/crash becomes delivery_unknown and is never resent
  automatically. Partial/blocked/unknown batches retain recipient ordering.
- [x] Exact /new, /help, /stop, /start and Hebrew aliases execute without a model.
  Reset preserves history; stop applies at intake and old pending replies never
  reappear after start. Unsupported media gets static text, without downloads.
- [x] Worker ownership/shutdown covers sends and engine jobs; disabled behavior
  remains compatible. Secrets, bodies, sender IDs and raw provider errors do not
  enter ordinary logs or receipts.
- [x] Tests use local SQL and fake provider/identity/engine only, cover failures,
  ordering, authorization, windows, controls, migrations and app lifecycle.
  make check, git diff --check and documentation links pass.

## Out of scope

Customer Google/JWT account linking, live number/account setup, live sends,
templates/campaigns, media downloads, account/BSUID variants, operator review UI,
automatic replay/reconciliation without a verified provider ID, and deployment.

## Commands / report

Write failing tests first, run focused tests, then make check and
git diff --check. Report using AGENTS.md section 9; distinguish local verified
sending behavior from actual account delivery and the remaining WA-06 pilot.

## Implementation result

Completed WA-05 on 2026-10-16. Logical answers remain immutable; a separate SQL
planner freezes provider parts and original ownership. The existing connection
supervisor owns the bounded outbound pump and HTTP client. Attempts reauthorize
the original recipient, enforce the service window and suppression, and persist
acceptance separately from authenticated delivered/read observations. Uncertain
submission is never automatically replayed. Static controls bypass the engine.

Validation: final `make check` passed with **1,420 tests**, including **93 new
cases** compared with the 1,327-test baseline. Formatting passed for 356 files,
lint passed, mypy passed for 352 files, and generated-stub checks passed.
The 27 warnings are the prior 24 warning instances plus three Alembic warnings
from the added migration upgrade/downgrade coverage; no new warning category or
warning suppression was introduced. `git diff --check`, document whitespace,
and 57 local documentation links/anchors passed.

Tests cover exact Unicode/grapheme roundtrips and bounds; original-recipient
authorization and revocation between parts; immutable planning, recipient/part
order and fairness; retries/budget/window expiry; ambiguous send/cancellation/
lost acceptance commits; early/out-of-order callbacks and later delivery proof;
stop/start cutoffs, reset concurrency/history, static media/help replies;
real local ownership during send cleanup; reopened storage; historical migration
preservation; and signed HTTP intake through the worker to a fake provider with
one engine build. regex is now an explicit runtime dependency; no property-test
library or archived Meta SDK was added.

### Self-review / corrections

- Query filtering now excludes blocked preceding parts before the candidate
  limit, so one recipient's multipart retry cannot starve independent recipients.
- An earlier unplanned answer blocks later delivery for that recipient; a
  missing internal conversation owner cannot silently become a public reply.
- Both reset paths reconcile a receipt committed between inbox/binding reads;
  a delayed duplicate cannot clear a newer conversation. Deterministic
  regression tests failed before the fixes and passed afterwards.
- Callback aggregation now resumes remaining parts after verified delivery
  supersedes an earlier failed observation, without resending the accepted part.
- System/unknown provider events cannot extend the response window, and invalid
  provider bodies fail before I/O. Ownership loss before sending remains fatal
  immediately, rather than being swallowed by scheduling-error handling.
- The new HTTP fixture initially selected another example's shared plugin
  namespace, causing two existing CLI validation tests to fail in the full run.
  It now uses the canonical example consistently with existing HTTP fixtures;
  the combined lifecycle/CLI checks and final full gate pass.

Architecture verdict: conforms; no remaining blocker within this scoped stage.
New domain ports and additive settings/schema are documented by ADR 0010.
Engine, YAML, conversation HTTP and client identity signatures stay compatible.
Identity and data/tool enforcement remain client-owned; no organization identity
or access token is synthesized from WhatsApp message text.

### Actual files changed in WA-05

Created:

- This task and `docs/adr/0010-whatsapp-outbound-delivery.md`.
- `src/agent_manager/domain/channels/{delivery,controls}.py`.
- `src/agent_manager/application/channels/{delivery,controls}.py`.
- `src/agent_manager/infrastructure/channels/whatsapp/{sender,rendering}.py`.
- `src/agent_manager/infrastructure/persistence/channels/{delivery,delivery_projection,controls}.py`.
- `src/agent_manager/infrastructure/persistence/migrations/versions/0010_channel_delivery.py`.
- `tests/agent_manager/test_whatsapp_rendering.py`, `test_whatsapp_sender.py`,
  `test_channel_delivery.py`, `test_channel_delivery_storage.py`,
  `test_channel_controls.py`, `test_delivery_migration.py`,
  `test_whatsapp_delivery_lifecycle.py`.

Modified:

- `tasks/README.md`, `docs/README.md`, `docs/WHATSAPP_INTEGRATION_PLAN.md`,
  and `pyproject.toml`.
- `src/agent_manager/{config,composition}.py`.
- `src/agent_manager/domain/channels/models.py` and
  `src/agent_manager/application/channels/{processing,worker}.py`.
- `src/agent_manager/infrastructure/channels/whatsapp/normalization.py` and
  `src/agent_manager/infrastructure/persistence/channels/{intake,tables,repository}.py`.
- `tests/agent_manager/test_whatsapp_normalization.py` and `test_webhook_migration.py`.

No files were deleted. Previous uncommitted work was preserved.

### Limits / next task

No live Meta, model, MCP, Google login/JWT issuer or production database was
contacted. PostgreSQL remains unverified against a live server. Sender defaults
off; migration 0010 and explicit version/token/support/identity settings are
required before activation. The current account's API contract, error-code
fixtures and eligibility must be verified during preflight; current Meta
developer pages returned HTTP 429. No API version or account compatibility was
invented. Downgrade removes delivery evidence and requires a controlled live
rollback procedure before resuming sends.

Unknown/failed/blocked work requires operator reconciliation; there is no review
UI, automatic unknown-ID correlation, template/campaign sending, or live number
setup. Google/JWT account linking remains a separate trusted client integration
and is mandatory before an internal-information pilot. Next: WA-06 pilot
packaging/runbook and operational verification. No commit, PR or deployment.
