# Conversation Persistence Plan

## High-Level Goal

Add a database-backed conversation persistence layer for `agent_manager` so the
agent runtime can retain users, stable sessions, append-only message history,
and fast conversation snapshots for follow-up questions.

The persistence layer must be relational and backend-agnostic at the
domain/application layer. SQLite should work end-to-end first. PostgreSQL should
fit behind the same abstractions and be enabled by URL/driver configuration when
the optional PostgreSQL dependency is installed.

## Scope

- Persist generic users without assuming an auth provider.
- Persist stable conversation sessions.
- Persist an append-only cold message/event table.
- Persist one hot snapshot row per active session for fast prompt context
  reconstruction.
- Keep cold writes and hot snapshot updates in one repository transaction.
- Expose bounded conversation-context retrieval by session id.
- Add cleanup support for expired hot snapshots.
- Integrate `agent_manager` service/API/CLI configuration minimally and safely.

## Non-Goals

- No background scheduler unless a scheduler already exists.
- No live PostgreSQL test suite in this pass.
- No client-specific auth, tenant, or business rules in the runtime.
- No deletion of cold conversation history except future explicit retention
  policies.
- No invasive storage of every internal runtime trace event.
- No token-counting implementation unless an existing token counter is found.

## Proposed Architecture

`agent_manager.domain` defines pure dataclasses and repository protocols.
`agent_manager.application` orchestrates user/session/message persistence around
the existing engine call. `agent_manager.infrastructure.persistence` owns
SQLModel tables, SQLAlchemy sessions, migrations, and SQL repositories.

The cold message table is the source of truth. The hot snapshot table is a
cache: it can be deleted, expire daily, and be rebuilt from cold messages.

## Current Progress

- Existing infrastructure inspected.
- Existing persistence is minimal: `conversations` plus `messages`, SQLModel
  tables, async SQL repository, memory repository, and Alembic migration `0001`.
- Existing `ConversationService` inlines recent message history into the prompt.
- Existing `RunContext` already carries `run_id`, `conversation_id`, and
  `user_id`.
- Planning docs created before implementation, per request.
- Domain models now include users, sessions, rich cold messages, hot snapshots,
  and bounded conversation contexts.
- SQLite-backed SQL repository and memory repository implement the richer
  persistence contract.
- Alembic migration `0002` adds the new conversation persistence tables.
- `ConversationService` writes user and assistant messages with stable
  `session_id` and per-run `run_id`.
- `agentctl run` now uses `ConversationService`, so local CLI runs persist the
  incoming user message and final assistant answer.
- `agentctl run --session-id ...` reuses the provided session; when omitted it
  prints a generated reusable session id.
- `agentctl run --user-id ...` passes user identity for local runs. If omitted,
  the CLI uses the documented local testing default `local-user`.
- API create/send routes accept optional `user_id`; create accepts optional
  stable `session_id`.
- Configuration supports `AGENT_DB_BACKEND` and `AGENT_DB_URL`, with
  compatibility for `DATABASE_URL`.
- If `AGENT_DB_BACKEND` and `AGENT_DB_URL` are both missing, persistence is
  still enabled. The default backend is SQLite and the default URL is
  `sqlite+aiosqlite:///chat.db`, a persistent local file. The system does not
  default to in-memory persistence and does not silently disable persistence.
- Runtime history retrieval enforces `context_window`/`max_messages` and
  `context_max_chars`. `context_max_tokens` is exposed in settings and the
  repository contract, but is intentionally not enforced until token counting is
  implemented.

## Manual Follow-Up Test

Enable SQLite persistence:

```bash
export AGENT_DB_BACKEND=sqlite
export AGENT_DB_URL=sqlite+aiosqlite:///chat.db
```

Run two messages with the same stable session:

```bash
agentctl run \
  --config examples/full-test/agents.yaml \
  --session-id demo-1 \
  --user-id asaf \
  --message "My name is Asaf. Remember this for the next message."

agentctl run \
  --config examples/full-test/agents.yaml \
  --session-id demo-1 \
  --user-id asaf \
  --message "What is my name?"
```

`session_id` is the stable conversation id. `run_id` is generated internally for
each invocation. The second run loads prior messages from persistence and
injects them before the current user message. If `--session-id` is omitted, the
CLI prints a generated session id so it can be reused.

Persisted today:

- successful user messages
- final assistant responses
- user/session metadata
- hot snapshot rows for fast context retrieval

Not persisted yet:

- intermediate tool events
- intermediate agent/orchestrator events
- raw headers, credentials, or secrets

## How To Resume

1. Read this folder, especially `implementation-plan.md`.
2. Check `git status --short` for unrelated in-progress changes.
3. Continue from the first incomplete step in `implementation-plan.md`.
4. Keep this folder updated as code changes land.
5. Run focused tests first, then `make check`.
