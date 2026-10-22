# ADR 0006 — Prepare retried conversation turns once

- **Status:** Accepted for implementation following the user's approval of the WhatsApp plan on 2026-10-15.
- **Date:** 2026-10-15.

## Context

A durable channel can receive one provider message repeatedly. Deduplicating
the webhook does not close the crash between committing a manager user message
and storing its generated run/message IDs in the channel queue. Calling the
existing `prepare_turn()` again creates another turn and changes its history.

## Decision

Add an optional `idempotency_key` to `ConversationService.prepare_turn()`.
Preparation remains separate from execution: recovering a prepared turn never
authorizes executing it twice. Existing HTTP, CLI, and streaming contracts and
callers retain their current behavior.

An optional repository capability atomically commits a hashed-key receipt,
the user message, the selected branch head, and the snapshot. A receipt owns
one conversation, user, exact content, message ID, and run ID. Reusing its key
with another owner/conversation/content fails without changing history. The
receipt survives head changes and restarts; history is reconstructed from the
original user message's parent rather than the current head. Idempotent edits
are deliberately unsupported.

Register the durable run after the prepared message commits, then reconcile
that registration on a retry. A failure between those commits leaves a
recoverable receipt, not another user turn. Recovery does not reset run status
or token usage. A restored receipt bypasses new-turn budget checks because it
does not authorize additional work. New keys still obey the existing budget.

The memory and SQL repository adapters implement the capability. Custom
repositories may omit it; requesting idempotence then fails explicitly before
writing anything. SQL adds one receipt table through an explicit migration;
no schema is created during application startup.

## Consequences and alternatives

Channel workers must claim execution durably and reconcile ambiguous runs;
the preparation key is not an exactly-once execution guarantee. The engine,
YAML, prompts, and client auth contracts stay unchanged. Keys must be bounded
and are stored only as SHA-256 digests, not logged.

Deriving message IDs from a provider ID alone was rejected: it couples generic
conversation persistence to a provider and does not bind retries to original
content/ownership. A separate receipt write after appending was rejected
because it leaves the same crash gap. An in-memory cache cannot survive restart.

## Enforcement

Repository/service tests cover replay, conflicting input, branch changes,
concurrent same-key requests, failed appends, failed run registration, and SQL
restart. The full `make check` gate remains required.
