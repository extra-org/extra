# WhatsApp delivery review — WA-05

Date: 2026-10-16. Scope: [task 0019](../tasks/0019-whatsapp-delivery-and-controls.md),
reviewed and corrected under [task 0020](../tasks/0020-whatsapp-delivery-review.md).
This supplements the earlier [WA-01–WA-04 review](WHATSAPP_REVIEW.md).

## 1. Summary

Reviewed the opt-in sender, immutable delivery planning, recipient ordering,
fresh identity checks, suppression/window guards, callback projection, exact
controls, migration and supervisor/client lifecycles. The implementation keeps
provider delivery separate from the engine and from immutable logical answers.
One lifecycle defect was reproduced and corrected; the full validation gate
passes with 1,423 tests.

## 2. Blocking issues

**[P2, fixed] Cancellation could abandon channel client cleanup.** After the
worker had drained, the composition context awaited HTTP client close and then
identity client close directly. Cancelling application shutdown during either
await could return from the lifespan with that client still open; cancellation
during HTTP close could also skip identity close entirely. This affects resource
cleanup during deployment/restart rather than authorizing messages or repeating
an engine run.

The composition layer now owns a separate cleanup task, shields and joins it
through repeated caller cancellation, and propagates cancellation afterwards.
Identity close also runs in a finally block after sender close. The existing
worker drain/ownership ordering and sanitized ordinary close errors are retained.
No open blocking finding remains from the inspected scope.

## 3. Non-blocking issues

No additional actionable defect was confirmed in the reviewed delivery/control
paths. Existing operating limits remain explicit: failed/blocked/unknown work
needs operator reconciliation, and a live downgrade must preserve delivery
evidence before sending resumes. These are documented constraints, not newly
implemented recovery capabilities.

## 4. Architecture concerns

No boundary violation found. Client resource ownership stays in composition;
provider HTTP/rendering and SQL projection stay in infrastructure; processing
uses domain ports. The correction adds no runtime request state and changes no
engine, YAML, conversation HTTP, identity resolver or persistence contract.
[ADR 0010](adr/0010-whatsapp-outbound-delivery.md) remains applicable; no new
architectural decision or migration is needed.

## 5. Security concerns

The reviewed send path checks the original account and configured organization
again before each internal part, then rechecks persisted suppression/window
state after the identity await. Static controls contain no internal answer.
Callbacks correlate exact connection/system/provider ID/recipient, and positive
delivery/read evidence does not regress on late sent/failed observations.
Ambiguous sends are held rather than retried automatically. The fixed Graph
host, secret settings, body limits and sanitized error logging remain intact.

The customer's trusted identity plugin must still implement revocable account
linking and current permissions; transport signatures and sender IDs do not
grant organization access. Google/JWT account linking is not implemented by this
review. Data/tool authorization remains the responsibility of client plugins.

## 6. Testing gaps

Three new deterministic cases use events to suspend HTTP or identity close,
cancel shutdown twice, assert the lifespan remains pending, release cleanup,
and verify close order plus propagated cancellation. They cover sender enabled
and disabled. All three failed before the implementation correction and passed
afterwards. Existing ownership/send-cleanup and identity-error tests remain in
the focused verification set. Only local SQLite and fake provider, identity and
engine dependencies are used.

Validation: all 129 focused delivery, controls, Unicode, sender, worker and
lifecycle cases pass. Final `make check` passed: 356 files formatted, lint clean,
mypy clean for 352 files, **1,423 tests passed / 27 warnings**, and generated-stub
checks passed. The 27 warning instances are unchanged from the pre-review
baseline of 1,420 tests. No warnings were suppressed. An initial full-gate lint
failure (SIM117 in the new test's nested context managers) was corrected before
the passing gate. `git diff --check`, 50 local documentation links/anchors and
whitespace checks for all six changed files also passed.

Live Meta delivery, current account/version-specific protocol fixtures,
PostgreSQL against a live server, Google/JWT login and production credentials
were not tested. Process termination that prevents Python cleanup is outside
the cancellation guarantee; crash recovery still preserves uncertain attempts.

## 7. Suggested improvements

Continue with the already planned WA-06 pilot runbook and operational preflight,
including a concrete operator procedure for unknown/failed work, current account
contract verification and the identity integration prerequisite. Do not enable
an internal-information pilot until that trusted account-linking path exists.

## 8. Final recommendation

**Approve** the scoped delivery change with the lifecycle correction included.
The confirmed defect is fixed and the full gate passes. This review does not
activate a real account or certify a production rollout; the existing identity
and operational prerequisites still apply.
