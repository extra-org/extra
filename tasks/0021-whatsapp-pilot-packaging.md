# Task 0021 — WhatsApp pilot packaging and operation (WA-06)

## Goal / authorization

Implement the final planned stage requested by the user after the WA-05 review:
an administrator runbook, a deployable pilot example, operational monitoring,
and fake-provider evidence for the complete round trip and durable restart.
The user chose structured log lines for monitoring instead of a new HTTP or CLI
interface. The real-number smoke test remains a separately authorized
deployment activity.

## Read first

AGENTS.md; project-architecture, senior-python-engineering, testing,
documentation, code-review; the WhatsApp plan §6–§7, ADRs 0006–0010, task
0019/0020, and the existing channel implementation and tests.

## Scope / files allowed to change

- This task, tasks/README.md, docs/README.md, docs/WHATSAPP_INTEGRATION_PLAN.md,
  and new docs/WHATSAPP_RUNBOOK.md.
- New examples/whatsapp-pilot/: README, agent config and prompt, an allowlist
  identity resolver plugin, `.env.example`, `senders.example.json`, and a
  `docker-compose.yml` that starts `agent-manager` on durable storage.
- Under src/agent_manager/: new domain/channels/status.py and
  infrastructure/persistence/channels/status.py; existing
  application/channels/{worker,worker_options}.py, api/routes/whatsapp.py,
  composition.py, config.py.
- New tests/agent_manager/test_channel_status.py and
  test_whatsapp_pilot_rehearsal.py; existing test_whatsapp_webhook.py for
  rejection-log assertions.

## Acceptance criteria

1. The worker logs one bounded `channel status:` line per configured interval
   with inbox counts by attention state, the oldest active inbox age, pending
   delivery parts and their oldest age, and unknown, failed, and blocked
   delivery counts. No message text, sender identifier, token, or provider
   message ID appears in it. A failed status read is logged and never stops
   the worker.
2. Every rejected webhook request logs `whatsapp webhook rejected:` with a
   fixed reason (signature, subscription, encoding, content type, length,
   size) and no request content.
3. The interval is configurable and validated; the default needs no setting.
4. A fake-provider rehearsal proves: intake while the worker is disabled keeps
   the message queued; a restart with the worker and sender enabled on the same
   database answers and sends it exactly once; a signed `delivered` status
   callback is projected; a further restart sends nothing again.
5. The runbook documents preflight, configuration, the actual enablement order
   and pause states, deployment command and migrations, health, monitoring,
   restart, rollback, retention, and the manual smoke-test checklist, matching
   the implemented behavior.
6. The example configuration validates, contains no secrets, and keeps the
   agent free of approval-dependent tools.
7. `make check` passes.

## Out of scope

Real Meta credentials or traffic, the smoke test itself, Google/JWT account
linking, a metrics endpoint or dependency, templates, media, and any schema
migration.

## Commands / report

`make check`; `agentctl validate` for the example; report per AGENTS.md §9.

## Implementation result

- `SqlChannelStatusReader` reads grouped counts and oldest ages without
  selecting text or identifiers. The worker bounds each read to five seconds
  and logs the result each `EXTRA_WHATSAPP_STATUS_LOG_SECONDS` (default 60); a
  failed read logs its type.
- The webhook route logs a fixed reason for every rejection.
- `examples/whatsapp-pilot/` validates offline. Its allowlist resolver denies
  malformed entries, re-reads the file per lookup, and fails closed when the
  file is unreadable.
- The rehearsal test proves paused intake, a single answer and send after
  restart, a projected `delivered` callback, and no resend on a later restart.
- The runbook's rollback command was rehearsed on a temporary database:
  downgrading to 0006 removes the channel tables and keeps conversations.

### Self-review / corrections

- The example published port 8100 on all interfaces. The manager's web
  conversation API is anonymous by default, so that bypassed the WhatsApp
  identity check. The port is now bound to loopback, and the runbook requires a
  proxy that forwards only the webhook path.
- Compose `environment` overrode `EXTRA_DB_URL` from `.env`, pairing a
  PostgreSQL backend with a SQLite URL. The value now defaults only when unset.
- The rollback commands did not work inside the image, whose entrypoint passes
  other commands to `agentctl`. The runbook now opens a shell with
  `--entrypoint sh`.
- The status read had no bound inside the scheduling loop. A stalled database
  could delay scheduling and turn a graceful drain into cancellation. It now
  times out after five seconds.
- Delivery outcomes counted parts while blocked replies counted replies. All
  outcome fields now count replies; `outbox_pending` still counts parts.

### Limits / next task

- No automatic retention for channel data exists. Proposed next task: a
  retention job with a configurable period for channel rows.
- The status query scans delivery history without retention; acceptable at
  pilot volume.
- The real-number smoke test and organization account linking remain pending.
