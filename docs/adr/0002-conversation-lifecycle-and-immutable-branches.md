# ADR 0002 — Conversation lifecycle and immutable message branches

- **Status:** Accepted
- **Date:** 2026-08-15

## Context

Conversation messages were stored as one append-only list, even though the
schema already carried an unused `parent_message_id`. Cancelling a run left its
user message but the history API could not project the authoritative run state,
so the widget removed all evidence of the stopped turn. Editing an earlier user
message cannot safely update that row or reuse its descendants: every later
answer was produced from the original content.

The browser also did not receive a streaming response until the engine emitted
its first event. That made cancellation before the first model token dependent
on cancellation of an ordinary request handler rather than on the response-owned
stream generator.

## Decision

One `conversation_id` owns an immutable message tree.

- `ConversationMessage.parent_message_id` points to the preceding message in
  that branch.
- `ConversationSession.head_message_id` selects the active root-to-leaf path.
- A normal turn appends at the selected head. An edit appends a new user message
  at the edited message's parent and atomically selects it as the new head.
- Existing descendants remain stored and unchanged. A late assistant result is
  stored on its original branch but selects itself only if its parent is still
  the active head.
- Model context and the history API traverse only the selected ancestry path.
  The synthetic UI label `Generation stopped` is never persisted or passed to
  the engine.
- The engine's existing `run_id` and `RunRepository` remain authoritative for
  lifecycle state. Manager deployments inject a durable SQL adapter for that
  contract into both engine and conversation service. Preparing a turn registers
  its run before `turn_started`, so disconnecting before engine iteration can
  still make the run terminal rather than leaving it `running` forever.
- The manager retains each run's cumulative reported tokens in a conversation
  usage ledger before exposing an approval pause or after execution cleanup,
  including failure, cancellation, and resume. The engine persists usage a leg
  reported before it failed or was cancelled, and counts each leg once.
  A usage write and counter reset finish before cancellation cleanup continues,
  including a cancellation after storage committed but before readback returned.
  Each leg write carries a random leg key that storage records atomically with
  the totals. Retrying after a commit whose acknowledgement was lost adds
  nothing, and overlapping legs of the same run, which `begin()` permits, are
  each charged.
  Pending-approval and final stream events expose the cumulative usage of
  completed legs.
  Observations update the highest total for that run, rather than adding
  execution legs again. A completed answer's message carries its run's charge,
  so cleanup retention is best effort and a ledger failure never discards the
  answer or replaces the turn's error. Budget queries take the
  largest of the ledger total, historical message usage, and available engine
  run totals per run across all branches. A batched run lookup recovers missed
  charges after a retention failure and retries their durable retention; the
  known charge applies even while that retry fails. SQL run charges return the
  updated row inside the write transaction, without a fallible post-commit read.
  This preserves legacy messages without counting a final answer
  twice, and keeps charges after process-local run records expire.
- The manager prepares and persists the turn before returning the streaming
  response. Its first SSE frame is `turn_started`, carrying the authoritative
  `run_id` and `message_id`; graph execution is owned by the response generator.
- A terminal engine event is acknowledged before the run becomes `COMPLETED`,
  and the manager persists its assistant message before exposing that event to
  HTTP. A disconnect that wins before acknowledgement cancels the run instead
  of losing a queued final answer.
- Failed or disconnected stream mutations are never replayed automatically via
  the non-streaming endpoint; without a client idempotency key, delivery failure
  cannot prove that the original request made no state change.
- A suspended approval remains resumable when its SSE response ends. The
  conversation owner may explicitly terminalize it through the pending-approval
  cancellation route. Cancellation atomically competes with the approval claim,
  so either cancellation or resume wins, never both.
- Approval resumes used by the widget are response-owned SSE executions. New
  turns and resumed turns share the engine's owned graph-task cancellation
  path; resume retains the original run and transitions
  `PENDING_APPROVAL → RESUMING → RUNNING` before emitting work.

No separate `branch_id` is introduced. A branch is identified by its leaf/head
message, which is already a stable domain identity.

## Contract changes

- Conversation history items expose `message_id`, `run_id`, and `status`.
- Send/stream requests accept optional `edit_message_id`.
- Streaming adds a manager-owned `turn_started` event.
- Pending approvals add a separate `/cancel` lifecycle endpoint; cancellation is
  not represented as an approval decision or as `DENY`.
- Approval decisions add `/decision/stream`; its `resume_started` event carries
  the original `run_id`, and disconnecting it cancels the active graph task.
- Conversation sessions gain `head_message_id`; manager persistence includes
  durable run records. Because no database environment currently exists, no new
  migration revision was created; the existing fresh-database baseline revision
  was updated in place.
- The default persistent SQLite database is unchanged. Only an explicitly
  in-memory SQLite URL composes process-local conversation and run repositories
  and skips Alembic, so tests and throwaway processes open no file.
- Branch traversal stays a repository concern: the SQL adapter walks
  `parent_message_id` from the head in a recursive CTE that also carries the
  limit, and run lifecycle state is projected through one batched lookup, so
  reading history costs neither a full-session scan nor a query per run.
- Lifetime token accounting adds the internal repository operations
  `record_run_token_usage` and `get_token_usage_by_run`, and the
  `conversation_run_token_usage` table.
  Migration `0006` backfills available engine run totals associated with every
  conversation branch, including pending and cancelled runs without answers.
  `RunRepository` gains `add_leg_token_usage`, whose default for existing
  adapters adds without deduplication; the built-in adapters record the leg key.
  Migration `0006` also adds the `agent_run_token_legs` key table.
  The only HTTP change is additive: manager `pending_approval` and `final`
  stream events now carry `input_tokens` and `output_tokens`. There is no YAML
  contract change.

## Consequences

- Original content, completed descendants, pending approvals, and checkpoints
  remain immutable and addressable after an edit.
- Resuming an approval after its prompt was edited away attaches the result to
  the original user message without selecting that inactive branch.
- Retry, regenerate, and branch navigation can reuse the same tree by selecting
  or appending from a different message head.
- The active conversation view is linear even though cold storage is a tree.
- Token budget accounting remains cumulative across every executed branch,
  because editing does not refund model/tool work already consumed.
- Repository compare-and-append rejects concurrent head changes before starting
  a second execution on stale context.
- External side effects completed before cancellation are not rolled back.
- If an approval claim wins before cancellation, the cancellation request
  returns a conflict because execution may already have started.
- Conversation history and run state configured as in-memory SQLite are lost on
  process restart; that remains an explicit opt-out from the persistent default.
- Provider-reported usage observed before cancellation is retained. Providers
  that report usage only after a cancelled request cannot be counted exactly.

## Alternatives

Creating a new conversation and copying the inherited prefix was rejected: it
duplicates history, weakens message/run provenance, and makes branch navigation
look like unrelated threads. Adding both `branch_id` and parent links was also
rejected because the leaf message already identifies a branch.

## Enforcement

- Repository contract tests run the ancestry/head behavior against memory and
  SQL adapters.
- Service tests assert exact edited and cancelled model contexts.
- API and browser tests assert lifecycle projection, edit identity, immutable
  downstream replacement, cancellation display, and reload behavior.
