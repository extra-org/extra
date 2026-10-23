# Issue 151 — Enforce access before protected root execution

## Goal / authorization

Fix [issue 151](https://github.com/extra-org/extra/issues/151), as requested by
the user. Apply the existing access resolver policy to a protected root agent
or orchestrator before any prompt resolution, model invocation, or tool work.

## Context / read first

AGENTS.md, .ai/README.md; project-architecture, senior-python-engineering,
testing, runtime-engine, sidecar-auth-context, architecture-review,
documentation and git-workflow; task 0006 and docs/SIDECAR_CONTEXT_AUTH.md.
Use systematic debugging, test-driven development and verification before
completion. The issue's HTTP reproduction distinguishes root bypass from
already-working protected-child filtering.

## Scope / files allowed to change

- src/agent_engine/engine/langgraph/graph/graph_builder.py: gate the root using
  the existing request-scoped route filters before its executor is entered.
- src/agent_engine/engine/langgraph/engine.py: require the configured access
  plugin when building a graph with protected nodes, including Python callers.
- tests/api/test_protected_access_e2e.py: real application regressions using a
  fake model, covering root/child allow/deny and forged identity headers.
- tests/engine/test_protected_root.py: run/stream, agent/orchestrator roots,
  missing/invalid policy context, resolver failures, missing access plugin,
  streaming-console visibility and pending tool-approval replay.
- docs/SIDECAR_CONTEXT_AUTH.md: document root denial and identity semantics.
- This task and tasks/README.md.

## Requirements / acceptance criteria

- [x] Deny-all policy prevents protected root agent and orchestrator execution.
- [x] Permitted callers can execute protected roots using the same resolver
  and non-secret context projection as protected children.
- [x] Missing/invalid required context and resolver errors fail closed.
- [x] Denied roots perform no prompt, model or tool work and expose no route.
- [x] Denial is a fixed refusal using existing result/SSE shapes; child
  filtering and unprotected roots retain their existing behavior.
- [x] Missing access plugin is a clear startup error for protected graphs.
- [x] HTTP and engine regressions fail before the fix and pass afterwards.
- [x] Documentation reflects the implemented flow; make check passes.

## Out of scope

API authentication redesign, new identity claims or role rules, WhatsApp
changes, new HTTP/SSE contracts, configuration rejection of protected roots,
new persistence or dependencies, unrelated documentation cleanup, and merging.

## Commands / expected final report

Run the new regressions before implementation, the relevant engine/API tests,
make check, and git diff --check. Report using AGENTS.md section 9, including
test counts, the denial response, and the limits of the existing identity
boundary. Preserve the user's original checkout and work.

## Implementation / validation result

The compiled root entry applies the existing route filters before calling its
agent or orchestrator executor. Denial returns `Access denied.` and an empty
route using the existing result/SSE shapes. The wrapper captures startup
collaborators and receives request state as an argument; no request decision is
cached. Access plugin loading now fails at startup for a protected graph even
when a Python host bypasses the API's configuration validator.

The initial regression run had 17 expected failures reproducing root execution
and missing-plugin acceptance, with nine existing allow/child/public behaviors
passing. Four additional prompt-plugin and per-request decision cases failed
when the root gate was temporarily bypassed, then passed with it restored.
The initial fix added 30 cases in the two test files. Its make check gate passed:
format/lint, mypy (293 files), 1,085 tests / 9 dependency warnings, and generated
stub checks. The first gate attempt caught import ordering and an unescaped
test regex; both were corrected before the passing run. All external models
are faked; storage uses temporary local paths. The final make check rerun also
passed after the wrapper's captured filters were made immutable; make validate
passed for both configured examples. git diff --check and new-file whitespace
checks passed.

The fix preserves YAML, plugin signatures, HTTP schemas and SSE event types;
it corrects enforcement of the existing protected-node contract, so no new ADR
or migration is required. General API authentication and older project-status
documentation remain separate concerns. No commit, PR, merge or deployment
was performed during the initial implementation.

## Post-implementation code review

The user requested this review after the initial fix. Review date: 2026-10-22.

### 1. Summary

Reviewed root/child policy semantics, request isolation, startup failure,
stream consumers, lifecycle, and checkpointed approval replay. One display
regression was confirmed and corrected; no open blocking finding remains in
this scope.

### 2. Blocking issues

**[P2, fixed] Streaming clients could display a blank refusal.** Returning
only a final event skipped the answer sink. `agentctl run --stream` and the
local/remote streaming chat consumers render answer deltas, so the denial was
invisible. A regression using the real local chat consumer reproduced only
`Agent > ` without the refusal; both HTTP streaming cases also lacked the
expected delta. All three cases failed before correction. The denied-root
branch now emits its fixed, non-model-generated answer through the existing
request-scoped answer sink, followed by the usual final event.

### 3. Non-blocking issues

No additional actionable defect was confirmed in the scoped changes. Broader
API authentication and legacy project-status prose remain outside this task.

### 4. Architecture concerns

The guard runs before either protected root executor. It reuses the existing
access filter and keeps request decisions in invocation state; only immutable
startup filter references are captured by the compiled wrapper. The correction
uses an existing stream sink and event type. No new YAML/plugin/HTTP schema,
engine request state, persistence, dependency or ADR is introduced.

### 5. Security concerns

Denied new runs expose no visited path and execute no prompt plugin, model or
tool. Missing/invalid policy context and resolver failures remain fail closed.
Permitted and denied requests on one engine do not cache grants between calls.
Eight additional approval-replay cases cover root agents/orchestrators and
normal/streamed resumes: unchanged policy permits one real local tool effect;
policy revocation prevents that effect and the existing invocation-binding
guard rejects the decision, fails the run and leaves no pending approval or
recoverable success. This tests resolver policy rechecks, not a redesign of
identity propagation or the host's authentication boundary.

### 6. Testing gaps

The review added nine cases, bringing the two new files to 39 cases. The
streaming console regression uses the real consumer and engine; replay tests
observe a real temporary-file tool effect. Models are deterministic fakes.
Disabling the root gate made the replay-revocation regressions fail, confirming
they exercise enforcement. No confirmed coverage gap remains for this scope.

The final make check passed: format/lint, mypy (293 files), **1,094 tests /
9 dependency warnings**, and generated-stub checks. make validate passed for
both configured examples; git diff --check passed. No warnings were suppressed.
One intervening full gate caught a stale source snapshot restored by the
mutation probe: the two HTTP streaming tests and console regression failed.
The stream correction was restored; the passing final run also verified its
source hash stayed unchanged throughout the gate.

### 7. Suggested improvements

No further change is required before reviewing this scoped fix for a PR.
Keep general authentication and outdated overview status documentation in
separate work.

### 8. Final recommendation

**Approve** the scoped fix with the streaming correction and added replay
coverage. This is a local self-review recommendation, not a GitHub approval.
No commit, push, PR, merge or deployment was performed during that review.

## PR preparation

On 2026-10-22, the user requested a pull request for the reviewed fix. This
authorizes committing and pushing the scoped change and opening a PR against
`main`. The PR includes the implementation, 39 regression cases, access-policy
documentation and this task record. Merging and deployment remain separate.

Fresh pre-commit validation passed: make check (1,094 tests, 9 dependency
warnings, format/lint, mypy and generated-stub checks), make validate (both
configured examples), and git diff --check. The reviewed source hash was
unchanged throughout the gate.

## PR 154 review follow-up — Fresh approval authorization

The user's 2026-10-23 request to fix PR 154 authorizes addressing
https://github.com/extra-org/extra/pull/154#discussion_r4360627342 and updating
the existing branch. The root guard currently reads checkpointed roles; the
manager forwards only the new credential on approval. A policy that reads
roles or scopes can therefore accept a revoked grant.

Scope additions required for this correction:

- engine/approval_engine.py and engine/approval_streaming_engine.py: an optional
  typed RunContext on the existing Python approval ports, documented in ADR 0002.
- engine/langgraph/engine.py: use fresh context while preserving approval-owner
  binding; missing fresh permission facts must never fall back to checkpoints.
- engine/langgraph/graph/graph_builder.py and runtime/hooks/context.py: project
  the active request's non-secret context into root/child execution on replay.
- agent_manager/application/conversation_service.py: supply the current verified
  Principal for both approval paths using the existing RunContext model.
- tests/api/test_protected_access_e2e.py and tests/engine/test_protected_root.py:
  real HTTP and engine revocation/allow/omitted-context regressions.
- tests/agent_manager/test_api.py: update approval doubles to the additive port.
- tests/api/test_run_context.py: follow the shared non-secret projection helper
  when it moves out of the engine module; keep the credential assertion intact.
- docs/SIDECAR_CONTEXT_AUTH.md, ADR 0002 and this task: describe semantics,
  compatibility, scope and verification. No HTTP/YAML/plugin schema change.

Acceptance criteria:

- [x] Both approval endpoints use the current verified principal's roles.
- [x] Protected agents/orchestrators and children reject revoked permissions
  before the pending tool effect, including streaming replay.
- [x] Fresh scope/metadata facts replace old authorization facts; missing facts
  do not inherit a grant from the original run.
- [x] Still-authorized approvals execute the reviewed tool exactly once.
- [x] Owner binding, credential forwarding, checkpoint replay and cancellation
  remain intact; request context is never stored on the shared engine.
- [x] The reviewed symptom fails before the fix; make check and examples pass.

Merged current main (6b9ada3) after resolving the task-index conflict by
retaining both sets of entries. The combined baseline passed make check:
1,498 tests / 27 dependency warnings, format/lint, mypy and generated stubs.

### Follow-up result and validation

The manager now sends a current RunContext through both typed approval ports.
The engine validates caller/run/session consistency before claiming an
approval and binds the active request context to the original approved owner.
Only current role/scope/organization/metadata facts enter access checks. The
shared non-secret projection helper is reused on initial execution and replay;
root entry copies state locally so child filters receive the same fresh facts.
No checkpoint edit, model regeneration, global identity state or persistence
migration is required. ADR 0002 records the additive Python keyword and adapter
upgrade requirement; HTTP/YAML/access-plugin shapes are unchanged.

The valid initial regression run reproduced six HTTP revoked-role cases that
executed the tool, plus two omitted-context resumes that reused old roles;
the six still-authorized HTTP cases already passed. An earlier HTTP test setup
used the engine's free-text decision alias instead of the HTTP enum; that was
corrected before observing the security failures. The final suite adds 40 cases
in total, including scope, metadata, organization, ownership and concurrent
approval isolation; the two protected-access files now contain 79 cases.

Final make check passed: **1,538 tests / 27 dependency warnings**, 363 files
formatted, lint, mypy (359 files) and generated-stub checks. Both make validate
examples and the WhatsApp pilot configuration passed; git diff --check passed.
An intermediate gate caught stale helper imports and test typing after adding
the keyword; these were corrected before the passing gate. No warnings were
suppressed, and all models/providers are fakes with temporary local storage.

### Follow-up code review

1. **Summary:** The checkpointed-authority bypass is corrected for root and
   child access checks on normal and streamed approval replay.
2. **Blocking issues:** The reviewed revoked-role tool effect is fixed; no
   additional blocking finding was confirmed in the scoped changes.
3. **Non-blocking issues:** Removing a protected child can reach the existing
   checkpoint mismatch error path (sanitized HTTP 500 or SSE error), while root
   denial uses the existing approval mismatch response. Both stop the effect
   and fail the run; changing those public error policies is separate work.
4. **Architecture concerns:** The manager supplies verified identity through
   typed ports; the engine remains generic, startup-scoped and free of request
   state. Replayed work and current authority remain separate.
5. **Security concerns:** Missing fresh facts never inherit old grants. Caller
   identity conflicts fail before claiming the approval. Token exclusion,
   original invocation binding and independent concurrent contexts are covered.
6. **Testing gaps:** Real manager HTTP verification and engine replay use fake
   models and observable local tool effects. The full gate covers existing
   cancellation, duplicate decisions, credential forwarding and nested replay.
   No live identity/provider deployment was exercised.
7. **Suggested improvements:** External approval adapters must accept the
   new context keyword; hosts must supply a trusted current snapshot.
8. **Final recommendation:** Approve the scoped correction after independent
   review. This is a local self-review, not a GitHub approval or merge.
