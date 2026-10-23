# Task 0020 — Review and harden WhatsApp delivery lifecycle

## Goal / authorization

The user explicitly requested a code review and fixes for any gaps after WA-05.
Review delivery authorization, ordering, callbacks, controls, recovery and
shutdown without extending the approved integration scope.

## Read first

AGENTS.md, .ai/README.md; project-architecture, code-review,
senior-python-engineering, testing, documentation, architecture-review,
sidecar-auth-context; task 0019, ADRs 0007–0010 and the implementation/tests.
Use systematic debugging, test-driven development and verification before
completion. The existing public contracts and immutable logical answers stay
compatible.

## Scope / files allowed to change

- This task and tasks/README.md.
- New docs/WHATSAPP_DELIVERY_REVIEW.md and docs/README.md.
- src/agent_manager/composition.py: channel client cleanup only.
- tests/agent_manager/test_whatsapp_delivery_lifecycle.py: deterministic
  regression coverage for cancellation during client resource cleanup.

The suspected gap is cancellation after the worker has drained but while its
HTTP/identity clients are closing. Reproduce before changing implementation;
preserve the existing worker ownership/drain ordering and sanitized errors.
Record any unrelated findings without silently expanding the change.

## Acceptance criteria

- [x] Review architecture, recipient security, delivery state/ordering, exact
  controls, retry/recovery and lifecycle; record a structured eight-part review.
- [x] Reproduce any fixed behavior with a failing regression first.
- [x] Cancellation during either channel client close cannot skip cleanup or
  finish application shutdown before both clients finish; repeated cancellation
  remains safe and cancellation is propagated afterwards.
- [x] Ordinary close errors remain sanitized and do not skip the other client.
- [x] Sender-disabled behavior and existing engine/HTTP/plugin contracts remain
  compatible; no request state is added to the engine.
- [x] Focused tests, make check, git diff --check and documentation links pass.

## Out of scope

Google/JWT account linking, production activation, provider protocol expansion,
new delivery/review APIs, operator UI, PostgreSQL deployment and live sends.
No schema, YAML, engine, conversation HTTP or identity signature change.

## Commands / expected report

Run failing regressions, focused lifecycle tests and make check. Record exact
results and remaining limits in the review; final response follows AGENTS.md
section 9. Preserve all pre-existing uncommitted work.

## Implementation result

The review confirmed one P2 lifecycle defect: cancellation while HTTP or
identity close awaited could abandon cleanup, and HTTP cancellation could skip
identity close. Three regression cases failed before the fix. Composition now
owns and shields a cleanup task, joins it through repeated cancellation and
propagates cancellation only afterwards; identity close runs in finally.
Worker drain and ownership behavior, ordinary sanitized close errors, public
contracts and data storage are unchanged. No additional actionable defect was
confirmed in the inspected WA-05 paths.

Verification: 129 focused tests passed. Final make check passed: format 356
files, lint, mypy 352 files, **1,423 tests / 27 warnings**, generated-stub check.
Warning instances match the 1,420-test baseline; none were suppressed. The first
full-gate attempt caught SIM117 in the new test, fixed before the final pass.
git diff --check, 50 local documentation links/anchors and whitespace checks
for all six changed files passed.

Created this task and docs/WHATSAPP_DELIVERY_REVIEW.md. Modified composition.py,
test_whatsapp_delivery_lifecycle.py, docs/README.md and tasks/README.md. No files
were deleted and previous uncommitted work was preserved. The structured review
records architecture/security checks, the fixed finding, evidence and limits.
No new ADR or migration is needed for restoring the existing cleanup guarantee.

Next: WA-06 pilot packaging and operational preflight; the Google/JWT account
linking prerequisite remains separate. No live provider/model/MCP/production
database was contacted, and there was no commit, PR, activation or deployment.
