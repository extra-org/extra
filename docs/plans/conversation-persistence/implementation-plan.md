# Conversation Persistence Implementation Plan

## Steps

1. ✅ Define richer domain models and repository protocol.
2. ✅ Preserve current simple `Repository` compatibility or adapt callers.
3. ✅ Expand SQLModel tables with user/session/message/snapshot tables.
4. ✅ Add Alembic migration `0002_conversation_persistence.py`.
5. ✅ Implement SQL repository methods:
   - upsert/get user
   - create/get session
   - append cold message and update hot snapshot in one transaction
   - list messages
   - get/rebuild bounded context
   - delete expired snapshots
6. ✅ Update memory repository for service tests.
7. ✅ Update `ConversationService` to use stable `session_id`, generated `run_id`,
   optional `user_id`, and bounded context retrieval.
8. ✅ Update API schemas/routes to accept optional `user_id`, `session_id`
   compatibility, and expose persisted messages.
9. ✅ Update `Settings` to support `agent_db_backend` and `agent_db_url` while
   preserving `database_url`.
10. ✅ Add focused tests.
11. ✅ Run format/lint/typecheck/tests.
12. ✅ Update this plan with final status and TODOs.

## Files To Create Or Change

- `src/agent_manager/domain/models.py`
- `src/agent_manager/domain/repository.py`
- `src/agent_manager/domain/__init__.py`
- `src/agent_manager/application/context.py`
- `src/agent_manager/application/service.py`
- `src/agent_manager/config.py`
- `src/agent_manager/api/schemas.py`
- `src/agent_manager/api/routes.py`
- `src/agent_manager/api/app.py`
- `src/agent_manager/infrastructure/persistence/tables.py`
- `src/agent_manager/infrastructure/persistence/sql_repository.py`
- `src/agent_manager/infrastructure/persistence/memory_repository.py`
- `src/agent_manager/infrastructure/persistence/migrations/versions/0002_conversation_persistence.py`
- `tests/agent_manager/test_repository_contract.py`
- `tests/agent_manager/test_service.py`
- new focused persistence/context tests if useful

## Tests To Add

- SQLite schema initialization.
- Create/get user.
- Create/get session.
- Append message.
- Cold table receives message.
- Hot snapshot updates.
- Retrieve conversation context.
- Delete expired snapshots.
- Rebuild snapshot from cold messages.
- Same `session_id` across appends produces one snapshot row.
- Different `run_id` values can belong to the same session.

## Migration Strategy

Add a new Alembic migration. Do not rewrite `0001_initial.py`. Existing
`conversations` and `messages` can remain for compatibility, while the richer
conversation persistence uses new table names to avoid destructive migration
ambiguity.

## Rollout Strategy

Default to SQLite local development. Existing API paths continue working.
If `AGENT_DB_BACKEND` and `AGENT_DB_URL` are missing, the system still uses
persistent SQLite at `sqlite+aiosqlite:///chat.db`. It does not use in-memory
persistence and does not disable persistence by default.
`context_max_tokens` is available as a configuration field and repository
argument, but adapters ignore it until token counting exists.
Applications can opt into a different database by setting:

```bash
AGENT_DB_BACKEND=sqlite
AGENT_DB_URL=sqlite+aiosqlite:///chat.db
```

For local CLI testing, pass identity explicitly with `--user-id`. If omitted,
`agentctl run` uses `local-user`.

PostgreSQL remains URL-driven and should be documented as planned unless tested:

```bash
AGENT_DB_BACKEND=postgres
AGENT_DB_URL=postgresql+asyncpg://user:password@host/db
```

## Current Status

Implementation is complete for the SQLite-backed path and the backend-agnostic
repository contract. PostgreSQL is URL-compatible by design but not live-tested
in this pass.

Focused validation passed:

- `pytest tests/agent_manager/test_repository_contract.py tests/agent_manager/test_service.py tests/agent_manager/test_api.py tests/agent_manager/test_context.py tests/agent_manager/test_config.py`
- `pytest tests/cli/test_run_persistence.py`
- `pytest tests/agent_manager tests/cli/test_run_persistence.py`
- `ruff check src/agent_manager tests/agent_manager`
- `ruff check src/agentctl src/agent_manager tests/cli/test_run_persistence.py tests/agent_manager`
- `mypy src/agent_manager tests/agent_manager`
- `mypy src/agentctl src/agent_manager tests/cli/test_run_persistence.py tests/agent_manager`
- Alembic `upgrade head` against a temporary SQLite database.

Repository-wide `make check` status:

- `ruff check src tests` passed.
- `mypy src tests` passed.
- `pytest` collection is currently blocked by missing pre-existing modules:
  `echo` in `tests/cli/test_chat_command.py` and
  `examples.local_mcp_server` in `tests/examples/test_local_mcp_server.py`.
