# Security Sprints

Actionable remediation plan from the security audit (July 2026).
Each sprint is ordered — do them in sequence.

---

## Sprint 0 — Emergency Fixes (1-2 days)

Quick wins, low effort, high impact.

| # | Task | Files | Effort |
|---|------|-------|--------|
| 1 | **Generic error responses** — replace `str(exc)` with sanitized messages in all API error returns. Log full exceptions server-side only. | `agent_engine/api/app.py:243,297,332` `agent_manager/api/routes.py:64,115` | 2h |
| 2 | **Non-root Docker user** — add `RUN useradd -r -s /bin/false agent` and `USER agent` before entrypoint. | `Dockerfile` | 30m |
| 3 | **Request size limits** — add `max_length=4096` (or similar) to all `str` fields in request schemas. | `agent_manager/api/schemas.py` `agent_engine/api/app.py` | 1h |
| 4 | **Default host 127.0.0.1** — change default `--host` from `0.0.0.0` to `127.0.0.1` in both servers. | `agentctl/main.py:190` `agent_manager/config.py:29` | 30m |
| 5 | **Docker Compose** — add `docker-compose.yml` with `engine` (8090) and `manager` (8100) services, SQLite volume, health checks, `.env` passthrough. | `docker-compose.yml` | 30m |

**Definition of done:** `make check` passes. No `str(exc)` in HTTP responses. Docker runs as non-root. `docker compose up` starts both services.

---

## Sprint 1 — Auth & Access Control (1-2 weeks)

The critical missing piece.

| # | Task | Files | Effort |
|---|------|-------|--------|
| 1 | **Auth middleware** — implement a FastAPI dependency that validates API key or JWT bearer token. Apply to all routes in both APIs. | `agent_engine/api/app.py` `agent_manager/api/app.py` `agent_manager/api/deps.py` | 3-5d |
| 2 | **Wire auth context to AccessFilter** — populate `AuthContext` from the verified identity so `protected` nodes are actually enforced. | `engine/langgraph/engine.py:669-674` `engine/langgraph/filters.py:42-67` `docs/SIDECAR_CONTEXT_AUTH.md` | 2-3d |
| 3 | **Rate limiting** — add `slowapi` or equivalent. Per-IP and per-endpoint limits, especially on `/invoke` and `/stream`. | `agent_engine/api/app.py` `agent_manager/api/app.py` | 1d |
| 4 | **Security headers** — add middleware for `X-Content-Type-Options`, `X-Frame-Options`, `Strict-Transport-Security`, `Referrer-Policy`. | Both API app files | 2h |
| 5 | **Tighten CORS** — restrict `allow_methods` and `allow_headers` to specific values instead of `*`. | `agent_manager/api/web.py:24-25` | 30m |

**Definition of done:** All endpoints require valid credentials. `protected: true` nodes reject unauthenticated callers. `make check` passes.

---

## Sprint 2 — Input Hardening (1 week)

Defense-in-depth.

| # | Task | Files | Effort |
|---|------|-------|--------|
| 1 | **Server timeouts** — configure uvicorn `timeout_keep_alive` and request timeouts. | `agentctl/main.py:208` `agent_manager/cli.py:38` | 2h |
| 2 | **SSE disconnect detection** — abort engine processing when client disconnects mid-stream. | `agent_engine/api/app.py:264-303` `agent_manager/api/routes.py:91-119` | 1d |
| 3 | **Path validation** — verify `import_from_path()` resolves within the expected `plugins/` directory. | `loaders/_import.py:22-29` | 2h |
| 4 | **Dependency lockfile** — generate `uv.lock` or `pip-compile` output for reproducible builds. | `pyproject.toml` | 1h |
| 5 | **Log redaction** — add a redaction pass in `StructuredFormatter` for values matching secret patterns at DEBUG level. | `logging_config.py` `observability/providers/logging/provider.py` | 4h |

**Definition of done:** Timeouts configured. SSE cleans up on disconnect. Lockfile committed. `make check` passes.

---

## Sprint 3 — Production Readiness (ongoing)

Operational security.

| # | Task | Files | Effort |
|---|------|-------|--------|
| 1 | **TLS docs** — document that TLS must be handled by reverse proxy. Add `--ssl-keyfile`/`--ssl-certfile` options to `agentctl serve`. | `agentctl/main.py` `docs/` | 1d |
| 2 | **Health check** — add `GET /healthz` that returns 200 when engine is ready. Add `HEALTHCHECK` to Dockerfile. | `agent_engine/api/app.py` `Dockerfile` | 2h |
| 3 | **Audit logging** — log all authentication failures, approval decisions, and protected-node access attempts. | `agent_engine/api/app.py` `engine/langgraph/filters.py` | 1d |
| 4 | **Pentest checklist** — create a doc with test cases for an external security review. | `docs/SECURITY_PENTEST.md` | 4h |

**Definition of done:** Deployment docs include TLS guidance. Docker image has health check. Audit log covers sensitive operations.

---

## Appendix: What's Already Done Well

| Practice | Location |
|----------|----------|
| YAML secret scanning (keys + credential shapes) | `parsers/yaml/parser.py:483-498` |
| Sensitive argument masking in HITL approvals | `approvals/sanitization.py` |
| CORS default deny (empty origins list) | `agent_manager/config.py:33` |
| Request ID sanitization (regex + 64-char cap) | `agent_engine/api/app.py:44-56` |
| MCP auth headers never logged | `runtime/hooks/mcp.py:81-85` |
| Execution limits per run (cost guardrails) | `runtime/execution.py` |
| Access filter fail-closed on exceptions | `engine/langgraph/filters.py:51-67` |
| `.env` files gitignored | `.gitignore` |
| SQL injection prevention via ORM | `infrastructure/persistence/` |
