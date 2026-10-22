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
