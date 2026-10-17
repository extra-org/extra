# Task 0017 — Review and harden implemented WhatsApp stages

## Goal / context

The user requested an explanation of WA-01–WA-04, a code review, and fixes for
gaps in the implemented behavior. Review the existing integration as a whole;
keep outbound delivery and Google/JWT account linking in their planned stages.

## Read first

AGENTS.md, .ai/README.md; project-architecture, code-review,
architecture-review, senior-python-engineering, testing, documentation;
tasks 0013–0016, the WhatsApp plan, and ADRs 0006–0009. Apply the installed
Python review, systematic debugging, test-driven development, and verification
skills to findings and their regression tests.

## Scope / files allowed to change

- This task and tasks/README.md.
- docs/README.md, docs/WHATSAPP_INTEGRATION_PLAN.md,
  docs/adr/0009-channel-worker-ownership-and-recovery.md,
  and new docs/WHATSAPP_REVIEW.md (explanation and structured review).
- src/agent_manager/application/channels/worker.py.
- src/agent_manager/domain/channels/models.py.
- tests/agent_manager/test_channel_worker.py,
  test_channel_storage.py, and test_whatsapp_normalization.py.

Fix the cancellation path that releases ownership before active cleanup, and
reject normalized strings that PostgreSQL cannot store before a webhook batch
enters its transaction. Report failed ownership immediately while cleanup is
still running. No public API, plugin, YAML, or schema change is needed.
Preserve all previous work and keep fixes narrow.

## Requirements / acceptance criteria

- [x] Review architecture, identity, intake transactions, retries, execution,
  response recovery, and lifecycle; document concrete findings and limitations.
- [x] Cancellation of shutdown, including repeated cancellation, cannot release
  worker ownership before engine tasks and durable bookkeeping have finished.
- [x] A failed supervisor reports unhealthy immediately, including while slow
  engine cancellation cleanup is still running.
- [x] NUL-containing identifiers/text are rejected at the normalized channel
  boundary; one invalid message/status does not discard valid webhook siblings.
- [x] Regression tests fail before each fix and pass afterwards; use local SQL,
  OS locks, fake engine/identity, and no live Meta/model/MCP/production database.
- [x] Explain implemented stages and explicitly distinguish staged answers from
  actual WhatsApp sending and the identity port from deployed account linking.
- [x] make check, git diff --check, and changed documentation links pass.

## Out of scope

WA-05 sending, provider reply formatting, product controls, WA-06 pilot setup,
Google/JWT account linking, a review/admin UI, distributed tool replay, live
PostgreSQL/Meta tests, deployment, commits, and unrelated refactoring.

## Commands / expected report

Run targeted regression tests, then make check and git diff --check. Record
the review in docs/WHATSAPP_REVIEW.md using the review workflow's eight sections.
The user-facing task report follows AGENTS.md section 9 and explains the current
product flow, fixed findings, remaining work, and verification limits.

## Implementation result

Completed on 2026-10-16. The review reproduced and fixed three product gaps:
caller-cancelled shutdown releasing ownership early (P1), NUL-containing input
passing the SQL-compatible channel boundary (P2), and healthy readiness while
a failed supervisor awaited slow cleanup (P2). Twelve new regression cases
failed before their respective corrections and passed afterwards. See
[the explanation and structured review](../docs/WHATSAPP_REVIEW.md).

The full gate also exposed existing test-owned engine leaks. These were kept
in the separate, bounded task 0018: its connection diagnostic improved from
38 open connections out of 42 to zero, with all 61 existing cases preserved.

Final `make check` passed: 1,327 tests, 24 baseline warning instances, formatting
for 339 files, lint, mypy for 335 files, and generated-stub checks. No aiosqlite
worker-thread warning remained in that final run. `git diff --check` and local
documentation links/anchors passed. No public contract, schema, engine, provider
integration, deployed identity, or production system was changed. The three
fixes conform to the existing architecture; outbound delivery/account linking
and the live pilot remain subsequent work.
