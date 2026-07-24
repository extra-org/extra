# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| `main` (beta) | Yes — active development, receives security fixes |
| < 1.0 releases | No — no stable releases yet |

This project is in **beta**. Security fixes are applied to `main` and shipped in the next container image build (`ghcr.io/extra-org/extra`). There are no long-term support branches.

---

## Reporting a Vulnerability

If you discover a security vulnerability, please report it **privately** — do not open a public GitHub issue.

**Preferred:** [GitHub Security Advisories](https://github.com/extra-org/extra/security/advisories/new)

### What to include

- Description of the vulnerability
- Steps to reproduce
- Affected version or commit
- Potential impact assessment
- Any suggested fix (if applicable)

### Response timeline

| Step | Target |
|------|--------|
| Acknowledgment | 72 hours |
| Triage and severity assessment | 7 days |
| Fix or mitigation | 14 days for critical, 30 days for others |
| Public disclosure | After fix is released, coordinated with reporter |

We follow **coordinated disclosure**. Please do not publicly disclose the vulnerability until a fix is available and you have been notified.

---

## Important: Beta Software — Known Security Gaps

This project is **not production-ready**. The following critical security gaps are known and documented. **Do not expose this software to an untrusted network without addressing them.**

| Gap | Risk | Status |
|-----|------|--------|
| **No authentication** on any API endpoint (`/invoke`, `/stream`, `/conversations`) | Critical | Open — Sprint 1 planned |
| **Access control not enforced** — the `protected` node feature is structurally wired but the Security/Context Gate that populates real identity is not implemented | Critical | Open — Sprint 1 planned |
| **No rate limiting** — no per-IP or per-endpoint request throttling | High | Open — Sprint 1 planned |
| **No built-in TLS** — must be handled by a reverse proxy | High | Open — Sprint 3 planned |
| **No security headers** — missing `X-Content-Type-Options`, `X-Frame-Options`, `Strict-Transport-Security` | Medium | Open — Sprint 1 planned |
| **No SSE disconnect detection** — engine continues processing after client disconnects | Medium | Open — Sprint 2 planned |
| **No server timeouts** — uvicorn keep-alive and request timeouts not configured | Medium | Open — Sprint 2 planned |

For the full remediation plan, see [docs/SECURITY_SPRINTS.md](docs/SECURITY_SPRINTS.md).

---

## Security Features Already Implemented

Despite the gaps above, the following security practices are in place:

| Practice | Description |
|----------|-------------|
| YAML secret scanning | Rejects literal secrets and credential shapes; only variable references are allowed |
| Non-root Docker user | Container runs as `agent` user, not root |
| CORS default deny | Empty allowed origins list by default; must be explicitly configured |
| Request size limits | Pydantic `max_length` on all string fields in request schemas |
| Execution cost guardrails | Per-run token and cost limits prevent runaway LLM spend |
| MCP auth headers never logged | Authentication headers passed to MCP servers are excluded from logs |
| SQL injection prevention | All database access goes through SQLAlchemy ORM |
| Safe YAML loading | YAML is loaded without code execution or arbitrary object construction |
| Sensitive argument masking | HITL approval prompts mask sensitive tool arguments |
| Request ID sanitization | Request IDs are regex-validated and capped at 64 characters |

---

## Deployment Security Recommendations

Before exposing Extra to any network, you **must**:

1. **Implement authentication** — write a custom auth plugin that validates API keys or JWT tokens. See the plugin architecture in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

2. **Run behind a reverse proxy** — use nginx, Caddy, or a cloud load balancer to terminate TLS and add security headers.

3. **Restrict CORS origins** — set `CORS_ORIGINS` in your `.env` to the exact domains that should access the API.

4. **Use Docker network isolation** — bind to `127.0.0.1:port:port` and expose through the container platform's networking.

5. **Monitor logs** — watch for authentication failures, approval rejections, and protected-node access attempts.

6. **Pin container versions** — use `ghcr.io/extra-org/extra:v1.2.3` (specific tag), not `:latest`.

---

## Security Roadmap

| Sprint | Focus | Status |
|--------|-------|--------|
| Sprint 0 | Emergency fixes (error responses, non-root Docker, request limits) | Completed |
| Sprint 1 | Auth & access control (authentication middleware, rate limiting, security headers) | Planned |
| Sprint 2 | Input hardening (timeouts, SSE cleanup, path validation, log redaction) | Planned |
| Sprint 3 | Production readiness (TLS docs, health checks, audit logging) | Planned |

Full details: [docs/SECURITY_SPRINTS.md](docs/SECURITY_SPRINTS.md)

---

## Security Update Process

- Security fixes are merged to `main` and released via [release-please](https://github.com/googleapis/release-please) automation.
- Container images are published to `ghcr.io/extra-org/extra` on each release.
- Users should subscribe to [GitHub Releases](https://github.com/extra-org/extra/releases) for notifications.
- For critical vulnerabilities, we will publish a security advisory on GitHub with affected versions and upgrade instructions.

---

## References

- [Security Sprints](docs/SECURITY_SPRINTS.md) — full remediation plan
- [Architecture](docs/ARCHITECTURE.md) — system design and security boundaries
- [Runtime Lifecycle](docs/RUNTIME_LIFECYCLE.md) — request flow and security gate
- [Plugin Context/Auth](docs/SIDECAR_CONTEXT_AUTH.md) — access control design
