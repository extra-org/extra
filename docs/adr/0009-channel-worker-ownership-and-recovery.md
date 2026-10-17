# ADR 0009 — Own channel recovery and never replay uncertain execution

- **Status:** Accepted under the approved WhatsApp plan and explicit WA-04 continuation.
- **Date:** 2026-10-16.

## Context

Inbox deduplication alone does not authorize execution. Manager preparation,
engine completion, response persistence, and outbox staging have separate
commit boundaries. Identity can be revoked while a message waits. Recovery
must not mistake a live worker or a completed run without an answer for work
that is safe to execute again.

## Decision

Keep the worker in the manager application, using the existing service and
durable channel/run ports. Extend the channel port with bounded, cursor-based
active-work scanning. Add an authorized service lookup for the deterministic
persisted assistant response; transports never derive its message ID or query
unowned conversation history themselves.

A supervised worker is separately opt-in and requires a configured organization
and trusted `module:Class` implementation of `ChannelIdentityResolver`. Load
and instantiate it once at startup; customer plugins own current account links,
credentials, and lookup resources. Resolver methods receive the opaque sender
address and return request-scoped principals. No Google/JWT implementation or
blanket organization identity is invented. Reauthorize after preparation,
immediately before claiming execution, and before recovering an internal answer.
The future sender must reauthorize again before delayed external delivery.

When an enabled supervisor stops, `/health` returns HTTP 503 with `status:
degraded` and the existing system field. Default worker-disabled deployments
retain their existing health response. This exposes supervisor failure to
readiness monitoring while preserving already accepted inbox records.
An ownership failure marks the supervisor stopped before awaiting child cleanup,
so a slow engine finalizer cannot keep readiness healthy after ownership loss.

One supervisor owns each connection before scanning active work. Local SQLite
uses a nonblocking POSIX file lock beside the canonical database path, released
by closing the descriptor. PostgreSQL uses a dedicated session advisory lock
and verifies the backend session while supervising; ownership loss aborts the
worker and cancels child tasks. Neither lock expires into a new execution claim.
See [Python flock](https://docs.python.org/3.12/library/fcntl.html) and
[PostgreSQL session advisory locks](https://www.postgresql.org/docs/17/explicit-locking.html#ADVISORY-LOCKS).
SQLite network filesystems/Windows and distributed tool replay remain unsupported.

Bound active jobs and polling; prioritize recoverable preparing/prepared/running
inbox items, then claim queued work. Per-sender binding ownership remains the
serialization boundary. Each active item is scheduled at most once locally;
the SQL prepared-to-running claim authorizes at most one engine invocation.

Recovery rules:

| Inbox / evidence | Action |
| --- | --- |
| Queued | Claim and prepare after current identity verification. |
| Preparing / prepared | Reuse WA-01 preparation, refresh identity, and claim execution once. |
| Persisted assistant response matching this conversation/run/user message | Stage that exact response with stable IDs; never regenerate it. |
| Running without a persisted response, including a completed run record | Hold for review; no automatic execution or tool replay. |
| Answered / done / denied / failed / review | Do not reopen automatically. |

On a staging/storage failure, retain running work so a later scan can recover
the stored answer. Identity denial after preparation instead holds review and
does not expose that answer. Empty or answers exceeding the current staging
part limit require review; provider-specific formatting/splitting is WA-05.
Unsupported media invokes no model and is recorded as failed; feedback is later
product work. Unexpected approval is cancelled through the manager's normal
owner-bound path; cancellation must be confirmed and its stream closed before
releasing the sender.

Shutdown stops new claims, drains existing tasks for a configured duration,
then cancels and awaits remaining streams before closing the manager and its
engine. An interrupted execution is held for review; preparation can be
reconciled using its existing receipt. Logs contain internal event IDs and
safe reason/type codes, never content, credentials, or raw plugin errors.
Cancellation cleanup is shielded and awaited even if it exceeds the drain
budget. The ownership lock is released only afterwards.
Cancellation of the lifespan caller itself also shields and joins the complete
shutdown task through repeated cancellation before propagating cancellation;
it cannot close the ownership stack while the driver is still draining.
Plugin lookup and cleanup methods are validated as async; cleanup errors are
logged by type without preventing manager/engine shutdown.

## Alternatives / consequences

Lease-based reruns and process-local sender locks were rejected. A persistent
owner row without a liveness boundary cannot safely distinguish restart from
another live process. Session/OS ownership provides that boundary while SQL
claims continue to prevent duplicate execution. This is still a single-owner
channel deployment, not distributed failover for the engine's process-local
approvals or tool ledger. Advisory-lock ownership requires a direct/session-
persistent PostgreSQL connection, not transaction-pooling middleware.

The internal repository/service additions are recorded here; existing
conversation HTTP, YAML, engine, and resolver signatures stay compatible. The
health status extension applies only to an enabled failed/stopping worker.
Enabling a worker without
a customer identity implementation fails closed. No outgoing transport is added.

## Enforcement

Behavior tests use local SQL and fake engine/identity adapters, exercising
fresh-context isolation, duplicate/restart boundaries, saved-answer recovery,
sticky ambiguous work, exclusive ownership, shutdown, and startup composition.
