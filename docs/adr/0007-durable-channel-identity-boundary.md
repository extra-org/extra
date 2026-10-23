# ADR 0007 — Durable channel work uses freshly resolved organization identity

- **Status:** Accepted for implementation under the user's approved WhatsApp plan and continuation request.
- **Date:** 2026-10-15.

## Context

WhatsApp messages can be duplicated or delivered while a previous turn is
running. The sender's channel identifier is not an organization login. The
confirmed deployment must access internal information, making anonymous or
phone-number-derived organization privileges unacceptable.

## Decision

Keep typed channel events and storage in `agent_manager`, independent of the
engine and provider payloads. Introduce SQL inbox, binding, and outbox tables
through migration 0008. A connection/provider-message key deduplicates input;
the original target system, sender, kind, content, and provider timestamp cannot
be rebound. A binding is scoped by connection, system, and opaque sender ID.

Each binding holds one active inbox claim. Atomic conditional updates serialize
its turns while allowing other bindings to progress. Preparing, prepared,
running, and review states keep that claim. Execution has a separate one-way
prepared-to-running claim. No automatic lease expiry or recovery reruns work.
A claimed reset advances the binding generation once; prior conversations are
retained. Staging one immutable ordered reply releases the sender for its next
turn, while delivery remains a later subsystem.

A client-owned `ChannelIdentityResolver` resolves the sender to a fresh
`Principal` for each preparation. The generic application verifies a nonanonymous
identity for the configured organization, and an existing binding cannot change
owners implicitly. Resolver failures and revoked/unlinked users fail closed.
Do not persist resolved credentials or role claims in channel rows, and do not
promote message text into identity fields. Normalized user text is retained as
message content. The returned principal remains request-scoped and reaches the
normal manager preparation and engine context boundary.

The selected future integration is Google login through a generic
JWT-issuing authentication server. JWT verification and revocable account
linking belong in that trusted client integration; this task implements the
provider-neutral port, without a login flow or JWT parser.

Use deterministic conversation IDs per binding generation and task 0013's
idempotency key per inbox event. Separate commits between conversation creation,
preparation, and queue bookkeeping are therefore recoverable without creating a
second conversation/user message. This does not authorize executing twice.

## Consequences / alternatives

The identity port is implemented by the customer's trusted integration; it is
not a login implementation or a blanket organization grant. Account-linking UI,
worker reauthorization before execution and delayed internal delivery, and
actual provider delivery remain later tasks. Reauthorization must preserve the
original recipient/owner rather than redirect old answers after relinking.
An unlinked message is denied without creating a manager user,
conversation, or turn. A rejection after preparation instead requires review.

SQL is required for durable channel work. Memory-mode manager deployments
continue to work and expose no channel repository. PostgreSQL uses the same
SQL adapter, but this task's local behavioral tests use SQLite and do not claim
live PostgreSQL deployment verification.

In-memory locks/caches were rejected because they do not establish durable
ownership. Persisting a resolved principal or credentials was rejected because
permissions can change between turns and secrets must not enter queue rows.
Rebinding a conversation after a phone/account change was rejected because it
would hand the old owner's history to a new principal.

## Enforcement

Tests cover SQL duplicates/concurrency/restart/rollback, stale claim tokens,
single execution claims, ordered reset, immutable reply staging, owner and
organization isolation, resolver errors/revocation, and secret-free persistence.
