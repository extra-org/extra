# WhatsApp pilot runbook

How an administrator deploys, operates, pauses, and rolls back the WhatsApp
channel for one organization-owned number. It describes the implemented
behavior from the [integration plan](WHATSAPP_INTEGRATION_PLAN.md) (WA-01 to
WA-06). The deployable example is in
[`examples/whatsapp-pilot/`](../examples/whatsapp-pilot/README.md).

No real account or number has been activated yet. The real-number smoke test
(§10) needs explicit authorization from the organization.

## 1. Preflight

Confirm all of these before enabling anything:

- [ ] The organization owns the number. If it already uses the WhatsApp Business
      app, check current coexistence or migration eligibility in Meta's
      documentation first. This channel does not import history.
- [ ] Business account ID and phone number ID for the Cloud API.
- [ ] Meta app secret, used to verify webhook signatures.
- [ ] A long-lived system-user access token with messaging permission. It must
      differ from the app secret and the verify token.
- [ ] A Graph API version the account supports, pinned explicitly (`vNN.0`).
- [ ] A public HTTPS URL that routes to `/channels/whatsapp/webhook` on the
      manager, and a webhook subscription to the `messages` field.
- [ ] Billing is ready for the account.
- [ ] A real support contact for `/help` and escalation.
- [ ] Identity: who may use the pilot and how senders are linked (§3).
- [ ] Durable storage: a SQLite file on a persistent volume for one host, or
      PostgreSQL.
- [ ] A retention decision for stored messages (§9).

## 2. Deploy

Run the conversation manager, not the engine API. The image's default
`agentctl serve` does not host conversations or the channel.

```bash
make build
```

Then, from `examples/whatsapp-pilot/` with a filled-in `.env`:

```bash
docker compose up -d
```

- **Expose only the webhook.** The manager also serves the web conversation API,
  which accepts anonymous visitors by default. Anyone who reaches that API can
  talk to the assistant without the WhatsApp identity check. The compose file
  therefore binds port 8100 to `127.0.0.1`. Your HTTPS reverse proxy on the
  same host should forward only `/channels/whatsapp/webhook` to it. If the proxy
  runs in a container, attach it to the compose network instead of publishing
  the port.
- `agent-manager` applies database migrations at startup (`--migrate`, the
  default). Migrations 0007–0010 add the channel tables.
- Storage defaults to SQLite on the `pilot-data` volume. `EXTRA_DB_BACKEND` and
  `EXTRA_DB_URL` in `.env` override it, for example for PostgreSQL.
- The queue and outbox live in the database. Keep its volume across restarts.
- Only one instance may run the worker for a connection. A second instance with
  the worker enabled fails at startup with `ChannelWorkerAlreadyRunning`.
  Other instances may run intake only.
- `docker compose stop` sends SIGTERM. The worker drains active turns for up to
  `EXTRA_WHATSAPP_WORKER_DRAIN_SECONDS`, so the compose file allows 30 seconds
  before the process is killed.

## 3. Identity

The worker resolves every sender through the configured identity plugin
(`module:Class`) at preparation, before execution, and before each send.
Message text never chooses the user, organization, or roles, and the principal's
organization must equal `EXTRA_WHATSAPP_ORGANIZATION_ID`.

The example plugin, `whatsapp_identity:AllowlistIdentityResolver`, reads a JSON
allowlist on every lookup. Keys are the sender IDs exactly as Meta sends them in
`from` (international digits without `+`). Removing an entry revokes access on
the next message. Possessing the phone is the only proof, so use it only for a
closed pilot. Replace it with the organization's account-linking resolver before
the assistant reaches internal information.

## 4. Configuration

All settings are environment variables, read at startup. Changing one requires
a restart.

| Variable | Stage | Meaning |
| --- | --- | --- |
| `EXTRA_WHATSAPP_ENABLED` | 1 | Mount the webhook and store incoming messages. |
| `EXTRA_WHATSAPP_CONNECTION_ID` | 1 | Internal name of this number (default `whatsapp`). |
| `EXTRA_WHATSAPP_ACCOUNT_ID` | 1 | Business account ID; entries for other accounts are ignored. |
| `EXTRA_WHATSAPP_PHONE_NUMBER_ID` | 1 | Receiving and sending number ID. |
| `EXTRA_WHATSAPP_APP_SECRET` | 1 | Verifies `X-Hub-Signature-256`. |
| `EXTRA_WHATSAPP_VERIFY_TOKEN` | 1 | Answers Meta's subscription challenge. |
| `EXTRA_WHATSAPP_MAX_BODY_BYTES` | 1 | Webhook body limit (default 262144). |
| `EXTRA_WHATSAPP_WORKER_ENABLED` | 2 | Run the turn worker. Requires stage 1. |
| `EXTRA_WHATSAPP_ORGANIZATION_ID` | 2 | Organization every sender must belong to. |
| `EXTRA_WHATSAPP_IDENTITY_RESOLVER` | 2 | Identity plugin, `module:Class`. |
| `EXTRA_WHATSAPP_WORKER_CONCURRENCY` | 2 | Senders processed in parallel (1–16, default 4). |
| `EXTRA_WHATSAPP_WORKER_POLL_SECONDS` | 2 | Queue polling interval (default 0.5). |
| `EXTRA_WHATSAPP_WORKER_TURN_SECONDS` | 2 | Time limit per turn (default 120). |
| `EXTRA_WHATSAPP_WORKER_DRAIN_SECONDS` | 2 | Shutdown drain limit (default 15). |
| `EXTRA_WHATSAPP_STATUS_LOG_SECONDS` | 2 | Interval of `channel status:` lines (default 60). |
| `EXTRA_WHATSAPP_SENDER_ENABLED` | 3 | Send answers. Requires stage 2. |
| `EXTRA_WHATSAPP_API_VERSION` | 3 | Pinned Graph API version, `vNN.0`. |
| `EXTRA_WHATSAPP_ACCESS_TOKEN` | 3 | System-user token for sending. |
| `EXTRA_WHATSAPP_SUPPORT_CONTACT` | 3 | Contact returned by `/help`. |

Startup fails if a stage is enabled without its prerequisites or required
values, or if storage is in-memory. Secrets are excluded from settings output.

## 5. Enablement order and pause states

Enable one stage at a time, and check health and logs before the next:

| State | Flags on | New messages | Pending work |
| --- | --- | --- | --- |
| Off | none | Webhook returns 404; nothing is stored. | Preserved. |
| Intake | 1 | Verified and stored as `queued`. | Waits. No status lines yet (they come from the worker). |
| Answering | 1, 2 | Answered and saved, not sent. | Answers wait as `answered`. |
| Live | 1, 2, 3 | Answered and sent. | Waiting answers are sent. |

Moving between states never deletes accepted work. To pause, set the flags for
the target state and restart. While intake is off, Meta's own retry policy
applies to callbacks it could not deliver; check its current documentation
before a long outage.

Free-form replies are allowed only within 24 hours of the user's latest
message. An answer that waits longer than that is blocked rather than sent, and
is counted in `delivery_blocked`.

## 6. Health and monitoring

`GET /health` returns 200 with `"status": "ok"`. When the worker is enabled but
has stopped, for example after losing exclusive ownership, it returns 503 with
`"status": "degraded"`.

The worker logs one line per `EXTRA_WHATSAPP_STATUS_LOG_SECONDS`:

```text
channel status: connection=whatsapp inbox_queued=0 inbox_running=1 inbox_answered=0
  inbox_oldest_s=4 needs_review=0 inbox_failed=0 outbox_pending=0 outbox_oldest_s=none
  delivery_unknown=0 delivery_failed=0 delivery_blocked=0
```

| Field | Meaning |
| --- | --- |
| `inbox_queued`, `inbox_running`, `inbox_answered` | Messages waiting, in progress, or answered but not yet sent. |
| `inbox_oldest_s` | Age of the oldest unfinished message; `none` when there is none. |
| `needs_review`, `inbox_failed` | Turns held for an administrator, or failed. |
| `outbox_pending`, `outbox_oldest_s` | Unsent reply parts (a long reply is split into several), and how long ago their message arrived. |
| `delivery_unknown` | Replies with an uncertain send outcome; never retried automatically. |
| `delivery_failed` | Replies the provider rejected. |
| `delivery_blocked` | Replies blocked by identity, the 24-hour window, or format. |

`needs_review`, `inbox_failed`, and the `delivery_*` fields are totals since
the database was created, so alert when they increase. Lines contain no message
text, sender IDs, tokens, or provider message IDs.

Every rejected webhook request logs `whatsapp webhook rejected: reason=<reason>`,
where the reason is `signature`, `subscription`, `encoding`, `content_type`,
`length`, `size`, or `incomplete`. A burst of `signature` rejections means a
wrong app secret or forged traffic.

Suggested alerts: health 503; `inbox_oldest_s` or `outbox_oldest_s` above a few
minutes; any increase in `needs_review`, `delivery_unknown`, or
`delivery_failed`; repeated `reason=signature`; `webhook intake failed` or
`channel supervisor stopped` warnings.

## 7. Restarts and recovery

A restart preserves every accepted message. On the next start the worker:

- continues `queued` and prepared messages;
- sends an answer that was saved but not yet sent, without generating it again;
- holds a turn that was running without a saved answer for review instead of
  running it twice;
- marks a send interrupted mid-request as `delivery_unknown` for review instead
  of risking a duplicate message.

Rehearse this before the pilot, as the automated
`test_whatsapp_pilot_rehearsal.py` does with a fake provider:

1. Start with intake only and send a test message: it is stored as `queued`.
2. Restart with all three stages: it is answered and sent once.
3. Restart again: nothing is sent twice, and the status line shows no pending
   work.

## 8. Rollback

To stop the channel but keep the current version, disable the flags (§5) and
restart. Data is kept.

To run an application version older than the channel migrations, downgrade the
schema first. **This deletes all channel data** (queue, bindings, outbox,
delivery history) while keeping conversations. Back up the database and stop
the manager. Then open a shell in the same image, volume, and settings; the
image's entrypoint would otherwise pass `sh` to `agentctl`:

```bash
docker compose run --rm --entrypoint sh manager
```

Inside that shell:

```bash
cd "$(python -c 'import agent_manager, pathlib; print(pathlib.Path(agent_manager.__file__).parent / "infrastructure/persistence/migrations")')"
```

```bash
alembic -c alembic.ini downgrade 0006
```

Without Docker, run the last two commands in the manager's Python environment
with the same `EXTRA_DB_URL`.

## 9. Data and retention

Stored channel data: normalized message text and sender IDs, reply text, sender
bindings and opt-out state, delivery observations, and webhook receipts (body
digest and issue codes, not raw bodies). Ordinary logs exclude message text,
sender IDs, and tokens.

There is no automatic deletion of channel data yet. Agree on a retention period
with the organization, and restrict database access, before the pilot. Automated
retention is a follow-up task.

## 10. Real-number smoke test

Only with explicit authorization, on the pilot number, with one allowlisted
tester:

1. Deploy with intake only. Confirm the subscription challenge succeeds in Meta.
2. Send a message: a `queued` row exists and no signature rejections are logged.
3. Enable the worker and restart: an answer is saved, not sent.
4. Enable sending and restart: the tester receives the answer once.
5. Send `/help`: the support contact arrives without a model call.
6. Send a message from a non-allowlisted number: it is marked `denied` and no
   answer is sent.
7. Stop and start: no duplicate messages. Record the results and the API version.
