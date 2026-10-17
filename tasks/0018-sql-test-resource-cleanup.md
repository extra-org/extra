# Task 0018 — Close SQL engines owned by existing repository tests

## Goal / context

The user's review-and-fix request exposed intermittent aiosqlite worker-thread
exceptions in the full gate. A diagnostic recording connection creation traced
unclosed connections to existing repository-contract fixtures and SQL
conversation-creation helpers. Keep this correction separate from task 0017's
WhatsApp implementation fixes. No product contract changes are involved.

## Read first

AGENTS.md, .ai/README.md, project-architecture, testing,
senior-python-engineering, systematic debugging, and verification skills;
the two test modules and persistence/database.py.

## Scope / files allowed to change

- This task and tasks/README.md.
- tests/agent_manager/test_repository_contract.py.
- tests/agent_manager/test_sql_conversation_creation.py.
- docs/WHATSAPP_REVIEW.md, to record the verification follow-up.

## Requirements / acceptance criteria

- [x] Demonstrate leaked live connections in these two modules before editing.
- [x] Test fixtures own and dispose every engine they create, including
  failure/assertion paths and repositories used through HTTP clients.
- [x] Preserve test assertions and the separate-connection concurrency setup.
- [x] Repeat the connection diagnostic: no owned live connections remain after
  the modules finish. Run the full make check and whitespace validation.

## Out of scope

Runtime/database behavior, dependencies, engine or manager public contracts,
other test modules, warning suppression, and WhatsApp product features.

## Commands / expected report

Use an in-process diagnostic around real local aiosqlite creation and the two
test modules, followed by make check and git diff --check. No live external
database, model, MCP, or Meta calls. Report this as test-resource cleanup,
separately from the three product corrections in task 0017.

## Implementation result

Completed on 2026-10-16. The pre-fix diagnostic ran the two modules successfully
but failed its resource assertion: 38 of 42 real local SQLite connections were
still open. Managed async fixtures/context managers now dispose each owned
engine, including test/setup failure paths. The same diagnostic then passed:
61 tests, 42 connections created, zero left open. Existing assertions and
independent-engine concurrency scenarios are preserved.

Final `make check` passed with 1,327 tests and the 24 prior dependency-warning
instances; formatting, lint, mypy, generated-stub checks, whitespace and doc
links passed. The earlier aiosqlite closed-loop thread warnings were absent.
No production source or contracts changed in this follow-up; no warnings were
suppressed, and no dependency or external service was changed.
