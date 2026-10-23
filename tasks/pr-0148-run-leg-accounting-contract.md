# PR 148 follow-up — Require atomic run leg accounting

## Goal / authorization

Fix the outstanding blocking review in
[PR 148](https://github.com/extra-org/extra/pull/148#discussion_r4303039830),
explicitly requested by the user on 2026-10-18. The review requires
RunRepository.add_leg_token_usage to be abstract rather than silently falling
back to non-idempotent addition. Update the existing ADR with this contract and
its compatibility impact.

## Context / read first

AGENTS.md, .ai/README.md; project-architecture, senior-python-engineering,
testing, architecture-review, runtime-engine, documentation and git-workflow.
Read RunRepository, both built-in adapters, the registration spy, usage
regressions and ADR 0002. Use failing regressions before implementation.

## Scope / files allowed to change

- src/agent_engine/runs/repository.py: require explicit atomic keyed accounting
  and remove the unkeyed default implementation.
- tests/runs/test_repository.py: reject legacy adapters at construction and
  ensure the base operation cannot silently charge usage.
- tests/engine/test_run_lifecycle.py: update its registration-only spy to the
  required contract without permitting lifecycle-owned token writes.
- docs/adr/0002-conversation-lifecycle-and-immutable-branches.md: contract and
  adapter migration guidance.
- This task and tasks/README.md.

The memory and SQL adapters already implement the atomic keyed operation; leave
their accounting behavior intact. No YAML, HTTP, schema or engine lifecycle
change. Do not reopen resolved review findings or include WhatsApp work.

## Acceptance criteria

- [x] A legacy adapter lacking add_leg_token_usage fails at construction with
  TypeError naming the missing required operation.
- [x] Explicitly calling the base operation raises NotImplementedError and
  cannot add unkeyed token usage.
- [x] All in-tree concrete adapters/test doubles satisfy the required contract.
- [x] Existing duplicate-key, ambiguous-commit and overlapping-leg regressions
  pass for the built-in adapters.
- [x] ADR 0002 states atomic (run_id, leg_id) claims and totals are mandatory,
  including the compatibility impact on external adapters.
- [x] Focused tests, make check and git diff --check pass.

## Out of scope

Additional token-accounting redesign, new adapters/migrations, WhatsApp changes,
review dismissal, merge, production deployment and live model/provider calls.

## Commands / report

Run failing contract regressions, focused repository/lifecycle/usage tests,
make check and git diff --check. Report using AGENTS.md section 9, including the
commit and updated PR, external-adapter migration requirement and validation.
Commit and push the scoped correction to the existing PR branch after
validation; do not merge or post review messages.

## Implementation / validation result

The operation is now abstract and its base body raises NotImplementedError;
it cannot silently invoke unkeyed addition. Built-in adapters already satisfy
the atomic keyed contract. The registration-only lifecycle spy now explicitly
rejects leg accounting, just as it rejects ordinary engine-owned token writes.
ADR 0002 records the required operation and external-adapter compatibility.
No request state, new persistence schema or client logic was added to the engine.

Both new contract regressions failed before the fix and passed afterwards.
The focused repository, lifecycle and conversation-usage set passed **101 tests**.
Final make check passed: format **297 files**, lint, mypy **293 files**,
**1,135 tests / 17 warnings**, and generated-stub checks. git diff --check passed.
Tests used fake models and local storage; no live provider calls were needed.

The first plain make check run had two CLI failures:
test_mcp_serve_validates_config_before_starting and
test_mcp_serve_starts_with_valid_config. The shared local chat.db is at revision
0010 from another branch, while this PR defines migrations through 0006. Both
tests passed when DATABASE_URL selected a temporary SQLite file. The final full
gate used the same isolated DATABASE_URL approach via TemporaryDirectory and
subprocess.run; the development database was neither downgraded nor removed.
No unrelated CLI or migration code was changed, and no warnings were suppressed.
