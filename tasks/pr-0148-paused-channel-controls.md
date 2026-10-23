# PR 148 follow-up — Enforce controls while sending is paused

## Goal / authorization

Address the user's 2026-10-22 request to fix PR 148 and its open review finding
at https://github.com/extra-org/extra/pull/148#discussion_r4350614733.
The worker must enforce suppression and state-changing controls independently
of outbound sending, including startup without outbound credentials.

## Read first

AGENTS.md, .ai/README.md; project-architecture, senior-python-engineering,
testing, architecture-review, sidecar-auth-context, documentation and
git-workflow. Read composition, ChannelControlService, ChannelTurnProcessor,
the SQL control adapter, lifecycle tests, ADR 0010 and the WhatsApp plan.
Apply systematic debugging, test-driven development and verification before
completion.

## Scope / files allowed to change

- src/agent_manager/composition.py: bind controls whenever the worker runs;
  keep the transport optional and reject missing durable control storage.
- src/agent_manager/application/channels/controls.py: permit an absent support
  contact while sending is disabled, without inventing a contact or model call.
- tests/agent_manager/test_whatsapp_paused_controls.py: real HTTP/file-SQL
  regressions with a fake engine and sender, including restart into live mode.
- docs/WHATSAPP_INTEGRATION_PLAN.md: clarify worker-only control behavior.
- This task and tasks/README.md.

## Acceptance criteria

- [x] Worker-only startup needs no outbound token, API version or support contact.
- [x] Signed stop suppresses subsequent input without invoking the engine.
- [x] Start permits only new input; restart into live mode does not send old
  suppressed replies or re-execute old input.
- [x] Reset starts a new conversation without prior history or a model call.
- [x] Help stays static; no configured contact means no fabricated help reply.
- [x] Missing durable control storage fails startup rather than bypassing controls.
- [x] Regressions fail before the fix; focused tests, make check and diff checks pass.

## Out of scope

Token accounting redesign, engine/YAML/API/plugin contracts, identity policy,
new settings or migrations, live provider calls, deployment and review approval.
Preserve the other agent's checkout and worktree. Update the existing PR branch
with a scoped commit after verification; never force-push or bypass reviews.

## Commands / report

Run new regressions before implementation, focused channel lifecycle/control
tests, make check and git diff --check. Verify the patch against the latest
main in isolation. Report using AGENTS.md section 9, including the updated PR,
verification results, remaining independent-review requirement and no live sends.

## Implementation / validation result

Controls are created for every enabled worker, independently of sender creation.
The worker rejects absent durable control storage. An optional support contact
preserves worker-only startup; absent contact means help completes without a
reply. Sending still requires the existing explicit credentials and contact.
No engine, persistence, YAML, HTTP or identity-plugin contract was changed.

All six new regression cases failed before implementation for the expected
control failures and passed afterwards. The focused lifecycle/control set
passed 43 tests. `make check` passed formatting, lint, mypy, **1,456 tests /
27 warnings**, and generated-stub checks. `git diff --check` passed. Tests use
temporary file SQLite, a fake engine and a fake provider; no live external
systems or development database changes were needed.
