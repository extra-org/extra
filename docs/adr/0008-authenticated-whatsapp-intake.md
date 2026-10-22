# ADR 0008 — Authenticate and commit webhook batches before acknowledging

- **Status:** Accepted under the approved WhatsApp plan and explicit WA-03 continuation.
- **Date:** 2026-10-15.

## Context

Provider callbacks are batched, duplicated, and retried. Authentic notifications
may contain malformed messages alongside valid siblings. A successful HTTP
response must not lose accepted input, and a transport signature grants no
organization identity or tool privileges.

## Decision

Add opt-in GET/POST `/channels/whatsapp/webhook` to `agent_manager`. GET checks
the verification token and returns the exact challenge. POST bounds streamed
bytes and verifies `X-Hub-Signature-256` using the separate app secret before
parsing. Neither endpoint uses browser authentication or interprets user JWTs.
Existing conversation API and engine contracts remain unchanged.

Settings require durable SQL and explicit account, number, and connection IDs.
The receiving system comes from the validated startup specification, never
from callback text. Separate provider Pydantic models normalize messages into
the existing channel domain. The initial verified envelope contract is
`whatsapp_business_account`, `entry[].id`, and
`changes[].value.metadata.phone_number_id`; message `from` stays opaque.
Unknown identity/account variants cannot implicitly gain a compatible mapping.

Migration 0009 adds authenticated webhook receipts and normalized delivery
events. A connection/body digest deduplicates callbacks. One transaction writes
the receipt, inbox inputs/bindings, delivery events, and bounded quarantine
reason/position metadata. Reuse the existing inbox insert helper. Conflicting
provider-message input is quarantined without altering its original target.
Database failure rolls the whole batch back and returns HTTP 503. HTTP 200
means durable intake only; it makes no execution or delivery claim.

Quarantine stores a digest and generated reason/position codes, without raw
webhook bodies, validation input, message snippets, profile data, or raw
provider errors. Valid normalized text remains in the inbox. This trades raw
payload forensic replay for reduced retention; operator investigation requires
provider-side diagnostics or controlled fixtures. Status callbacks persist
separately and never enter the agent queue; delivery projection is later work.

## Consequences / alternatives

The webhook is absent by default, and explicit enabling starts intake only.
There is no execution worker or sender in this task. A later worker must use
the mandatory client identity boundary before internal work.

Per-message commits and in-process background tasks were rejected: they cannot
atomically establish receipt of a whole callback. Rejecting every malformed
signed event was rejected because it causes poison retries. Unsigned events
are rejected before storage; oversized bodies are rejected rather than stored.

Current Meta developer pages returned HTTP 429 during research. The archived
[official webhook reference](https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/webhooks/start/)
supports the challenge/signature contract, and the
[official webhook types](https://github.com/WhatsApp/WhatsApp-Nodejs-SDK/blob/main/src/types/webhooks.ts)
document the original envelope/message fields. Meta's
[official Postman reference](https://www.postman.com/meta/whatsapp-business-platform/folder/tduohwq/webhook-payload-reference)
and [collection documentation](https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api)
were readable and verify the wire key `phone_number_id`, text input, and
separate status callbacks. The archived SDK itself is not used.
Current account-model/BSUID contracts and account eligibility
must be rechecked with the actual account before deployment. No API version is
invented for intake; outbound version selection remains a later prerequisite.

## Enforcement

Tests exercise byte-level signatures, streamed limits, token challenges,
target isolation, complete batches, malformed siblings, safe quarantine,
atomic rollback, concurrent duplicates, restart, schema migration, startup
composition, and absence of manager/engine execution during callbacks.
