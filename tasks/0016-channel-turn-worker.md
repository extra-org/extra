# Task 0016 — Channel turn worker and conservative recovery (WA-04)

## Goal / context

Implement the approved plan's next stage: a supervised, explicitly enabled
manager worker using durable channel claims and the existing conversation
service. The confirmed pilot accesses internal organization information;
Google/JWT account linking remains deferred behind the generic identity port.

## Read first

AGENTS.md, .ai/README.md; project-architecture, senior-python-engineering,
testing, architecture-review, runtime-engine, sidecar-auth-context,
documentation, code-review; the WhatsApp plan and ADRs 0006–0009.

## Scope / files allowed to change

- This task, tasks/README.md, docs/README.md, docs/WHATSAPP_INTEGRATION_PLAN.md,
  and new docs/adr/0009-channel-worker-ownership-and-recovery.md.
- Under src/agent_manager/: new application/channels/{processing,worker,worker_options}.py;
  existing application/channels/preparation.py and application/conversation_service.py.
- New domain/channels/ownership.py; existing domain/channels/{repository,identity}.py.
- New infrastructure/channels/identity_loader.py and
  infrastructure/persistence/channels/ownership.py; existing persistence/channels/repository.py.
- config.py, composition.py, api/app.py.
- New tests/agent_manager/test_channel_worker.py,
  test_channel_worker_lifecycle.py, test_channel_worker_ownership.py.

No schema migration is needed: bounded active-work scanning uses existing
states/claims. Add a service lookup for an authorized persisted run response;
do not reproduce manager response-ID logic in the worker.

## Requirements / acceptance criteria

- [x] Worker defaults off; enabling requires intake, durable SQL, an organization,
  and a trusted module:class identity resolver. Invalid plugin configuration
  fails startup without reflecting plugin exception text or credentials.
- [x] Exclusive connection ownership prevents concurrent recovery/supervisors:
  POSIX file lock for local SQLite; dedicated PostgreSQL session advisory lock.
  Ownership loss stops claims and cancels active work; no expiring execution lease.
  An enabled worker that has stopped makes /health return 503/degraded.
- [x] Bounded concurrency allows independent senders to progress while durable
  binding claims preserve each sender's order. Engine is reused from startup.
- [x] Preparation and execution use fresh external same-organization identities;
  owner changes/revocation fail closed. Updated roles/credentials reach only the
  request context, never channel persistence or ordinary logs.
- [x] Preparing/prepared work recovers via WA-01 receipts; an atomic execution
  claim allows one engine invocation. Terminal run state without a response is
  not permission to restart an already claimed execution.
- [x] Persisted assistant responses are recovered and staged with stable IDs,
  without regeneration. Running work with no persisted response stops for review.
  A failed outbox write can retry staging from the saved answer, never rerun tools.
- [x] Pending approvals are never approved: cancel through the normal manager
  path; unconfirmed cancellation requires review. Empty/missing/oversized
  answers require review and are not silently truncated. Unsupported media
  causes no model work; product feedback/control commands remain WA-05.
- [x] Shutdown stops claiming, drains bounded work, then cancels/awaits remaining
  streams before manager/engine/repositories close. Interrupted execution stays
  blocked for review; safe preparation remains recoverable.
- [x] Tests cover ordering/concurrency, ownership, identity refresh/revocation,
  restart/crash boundaries, answer recovery, failure/approval/timeout/shutdown,
  and app lifespan. No live Meta/model/MCP or production database calls.
- [x] make check, git diff --check, and documentation links pass.

## Out of scope

Provider sending/delivery projection, WhatsApp-specific rendering/splitting,
support/reset/opt-out product controls, login/account-linking implementation,
automatic reexecution of ambiguous runs, distributed tool/approval recovery,
live account setup, and deployment. SQLite ownership requires local POSIX
storage; network-mounted SQLite and Windows locks are not enabled here.

## Commands / report

Test first; run focused cases while developing, then make check and
git diff --check. Report using AGENTS.md section 9, distinguishing staged
answers from actual provider delivery and a plugin port from deployed identity.

## Implementation result

Completed WA-04 on 2026-10-16. The opt-in worker shares the startup engine and
normal conversation service. It loads a mandatory client identity plugin once,
refreshes authorization before execution/staging, owns one connection lifetime,
and processes bounded independent-sender jobs. Existing HTTP conversation,
YAML, and engine contracts remain unchanged. The enabled-worker health extension
and internal repository/service additions are recorded in ADR 0009.

Fresh validation: `make check` passed with 1,315 tests, including 47 new worker
cases; formatting, lint, and mypy passed (335 source/test files). The 24 existing
dependency warnings are unchanged from task 0015. `git diff --check` and 47
local documentation links passed. No schema change beyond migration 0009 was
needed or applied to a production database.

Coverage includes HTTP-to-worker history continuity with one engine build,
fresh credentials, revoked/malformed identity, bounded active scanning, stable
preparation receipts, all terminal-run states without an answer, reopened SQL
answer recovery, staging failure, independent senders/order, exclusive local
ownership, PostgreSQL session-change contracts, approval cancellation, missing/
oversized responses, execution timeout, shutdown and ownership loss.

Self-review found and fixed early sender release during approval stream cleanup,
missing failed-run metadata on a stream without a final response, repeated
cancellation interrupting slow cleanup, and a claim returning after shutdown
starting new work. Regression tests failed before each correction and passed
afterwards. Plugin cleanup failures are sanitized; failed supervisors report
503/degraded instead of healthy. No blocking findings remain within this scope.

Tests used file-backed SQLite and fake engine/identity adapters. PostgreSQL
ownership used a controlled connection double, not a live database; SQLite OS
locks were exercised locally. No Meta, model, MCP, deployed identity, real
number, or production system was contacted. Internal activation still requires
the customer's trusted account-linking/permission implementation. Google/JWT,
provider sending, delivery projection, support/reset/opt-out behavior, media
feedback, and deployment remain subsequent tasks. Next: WA-05 outbound delivery
and controls.
