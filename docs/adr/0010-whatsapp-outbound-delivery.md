# ADR 0010 — Persist provider delivery separately from logical answers

- **Status:** Accepted under the approved WhatsApp plan and explicit WA-05 request.
- **Date:** 2026-10-16.

## Context

WA-04 stores immutable logical replies. Provider rendering, retries and receipt
callbacks have different commit boundaries. An accepted send is not evidence
of delivery; a transport timeout is not evidence of rejection. Delayed internal
replies must not escape after account revocation or opt-out.

## Decision

Keep logical outbox rows intact, including replies staged before this migration.
Migration 0010 adds delivery batches and immutable provider parts, and sender
preference/window metadata. A bounded planner freezes parts once per event;
provider IDs and attempts live on those parts. No model is called for delivery.
UTF-16 units are a conservative application bound (4096 by default), with full
grapheme segmentation via an explicit regex dependency and paragraph preference.
Rendered bodies remain plain text; no Markdown-to-HTML conversion, truncation,
normalization or bidi rewriting occurs. Unrenderable/oversized batches stop.

The existing exclusive supervisor also owns the outbound pump. One network
submission is in flight at a time in this first single-number implementation;
engine jobs retain bounded independent-sender concurrency. Each part is claimed
durably before submission. Later parts/turns wait for prior acceptance; unknown,
failed or blocked batches stop that recipient while other recipients progress.
Startup/recovery marks abandoned sending attempts unknown, never ready again.
HTTP retries inside the client are disabled. Only a known rejection or failure
before submission may retry, with capped exponential backoff, jitter and attempts.
The adapter recognizes structured 429/5xx rejections and a structured 400 whose
`is_transient` flag is explicitly true; it does not guess a current Meta error
code catalog from third-party references. Other client errors are terminal.
Ambiguous I/O, malformed success, cancellation or lost acceptance persistence
requires review. No exactly-once external delivery is promised.

Before every attempt resolve a fresh identity for the original immutable
recipient/owner and configured organization, then recheck persisted suppression
and service-window state. Static control replies contain no internal data and
do not require a linked employee. They still obey suppression/window rules.
The HTTP adapter sends only to the fixed HTTPS Graph host with an explicit
deployment-selected API version; tokens are secret settings, not YAML, rows,
URLs, prompts or logs. There is no default production token or chosen API version.

Callbacks retain their existing authenticated intake. Projection joins exact
connection/system/provider-ID/recipient, including an early saved callback.
Read/delivered evidence does not regress on late sent/failed observations.
If verified delivery later supersedes an earlier failed callback, recompute the
batch from its parts so remaining parts can proceed without resending that part.
Accepted IDs never trigger automatic resends after delivery failure.
Unknown sends without a verified ID cannot be reconciled from guessed metadata.

Exact commands are normalized at the provider edge: /new or שיחה חדשה,
/help or נציג, /stop or הפסק, /start or המשך. Stop is durable at intake,
before acknowledgement, even if a model is still running. A sequence cutoff
permanently suppresses previous pending input/replies; start does not resurrect
them and does not grant organization access. Controls use the ordinary sender
queue, create no model runs and retain history on reset. Unsupported media
receives a static explanation without downloading data.

## Protocol evidence and limitations

Meta's [official archived text reference](https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/messages/text/)
and [message types](https://github.com/WhatsApp/WhatsApp-Nodejs-SDK/blob/main/src/types/messages.ts)
support the text body/preview flag, recipient, and returned message ID contract.
The [official messaging policy](https://business.whatsapp.com/policy) confirms
the 24-hour response window, opt-out and escalation requirements. Current Meta
developer message/error pages returned HTTP 429 during implementation research.
The application cap is conservative; the operator must verify the configured
version/account's actual contract and eligibility during WA-00/WA-06. The
archived SDK is not installed. No unverified callback correlation field or
account variant is enabled, and no current Graph version is guessed.
System/unknown event kinds cannot open the service window. Existing unsupported
rows are excluded from the conservative window backfill because their original
provider message type was not retained.

## Consequences / alternatives

A new provider layer avoids mutating already-staged answers or coupling generic
manager completion to WhatsApp. A broker/distributed sender is unnecessary for
this pilot. Expiring send leases, blind timeout retries and regenerating answers
were rejected because they can duplicate messages or tool effects.

Internal channel ports and additive settings/schema are extended under this
approved stage. Existing engine, YAML, conversation HTTP and identity resolver
signatures remain compatible. Sender is separately opt-in and cannot run without
the existing supervised worker. Customer account linking and live activation
remain separate prerequisites.

Downgrade preserves the previous logical inbox/outbox schema but removes new
delivery evidence. A live rollback must preserve/restore that evidence and
reconcile any submitted attempts before re-enabling sends; dropping tables and
replanning old replies is not a safe delivery recovery procedure.

## Enforcement

Local SQL and fake HTTP/model/identity tests cover immutable planning, Unicode
roundtrip/bounds, recipient order, CAS/restart, callback order, failure taxonomy,
fresh identity/suppression/window checks, controls, shutdown and migration.
