# WhatsApp pilot

A deployable starting point for the WhatsApp channel: one tool-free assistant,
a sender allowlist, and the conversation manager on durable storage. Read the
[runbook](../../docs/WHATSAPP_RUNBOOK.md) before enabling anything.

| File | Purpose |
| --- | --- |
| `agents.yml`, `prompts/assistant.md` | The assistant. It has no tools, so it never waits for an approval that WhatsApp cannot answer. |
| `whatsapp_identity.py` | `AllowlistIdentityResolver`: links listed senders to pilot users. For a closed pilot only. |
| `senders.example.json` | Allowlist format, keyed by the sender ID Meta sends in `from`. |
| `.env.example` | Every setting, with all three stages off. |
| `docker-compose.yml` | Runs `agent-manager` with the database on a durable volume. |

## Start

```bash
cp .env.example .env
```

```bash
cp senders.example.json senders.json
```

Fill in `.env` and `senders.json`, build the image from the repository root
with `make build`, then:

```bash
docker compose up -d
```

Turn on the stages one at a time, in this order, restarting after each:
intake (`EXTRA_WHATSAPP_ENABLED`), worker (`EXTRA_WHATSAPP_WORKER_ENABLED`),
sending (`EXTRA_WHATSAPP_SENDER_ENABLED`).

The manager listens on `127.0.0.1:8100` only. It also serves the web
conversation API, which accepts anonymous visitors by default, so do not publish
the port. Configure your HTTPS reverse proxy to forward only
`https://<your-host>/channels/whatsapp/webhook` to it.

`senders.json` and `.env` contain personal data and secrets. Keep them out of
version control.
