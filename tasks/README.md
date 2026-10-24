# Tasks

Small, ordered implementation units. Work them **in numeric order** unless told
otherwise — later tasks depend on earlier ones. Each task is intentionally
narrow; do only what its scope says and propose new tasks for discovered work.

Every task file has: **Goal, Context, Scope, Files allowed to change,
Requirements, Out of scope, Acceptance criteria, Commands to run before
finishing, Expected final report.**

| #    | Task                                                       | Depends on |
| ---- | ---------------------------------------------------------- | ---------- |
| 0001 | [Repository foundation](0001-repository-foundation.md)     | —          |
| 0002 | [YAML schema & validation](0002-yaml-schema-and-validation.md) | 0001   |
| 0003 | [Compiled agent graph](0003-compiled-agent-graph.md)       | 0002       |
| 0004 | [Runtime engine](0004-runtime-engine.md)                   | 0003       |
| 0005 | [Prompt rendering](0005-prompt-rendering.md)               | 0004       |
| 0006 | [Plugin context/access](0006-sidecar-auth-context.md)      | 0004, 0005 |
| 0007 | [MCP tools & plugin tools](0007-mcp-tools-and-permissions.md) | 0004, 0006 |
| 0008 | [CLI](0008-cli.md)                                          | 0002–0004  |
| 0009 | [API server](0009-api-server.md)                           | 0004       |
| 0010 | [Docker deployment](0010-docker-deployment.md)             | 0009       |
| 0011 | [Observability & tracing](0011-observability-and-tracing.md) | 0004     |
| 0012 | [Tests & quality gates](0012-tests-and-quality-gates.md)   | all        |
| 0013 | [Idempotent conversation turns](0013-idempotent-conversation-turns.md) | conversation lifecycle; WhatsApp WA-01 |
| 0014 | [Channel storage and identity](0014-channel-storage-and-identity.md) | 0013; WhatsApp WA-02 |
| 0015 | [Authenticated WhatsApp webhook](0015-whatsapp-webhook.md) | 0014; WhatsApp WA-03 |
| 0016 | [Channel turn worker and recovery](0016-channel-turn-worker.md) | 0015; WhatsApp WA-04 |
| 0017 | [WhatsApp review and hardening](0017-whatsapp-review-and-hardening.md) | 0013–0016 |
| 0018 | [SQL test resource cleanup](0018-sql-test-resource-cleanup.md) | 0017 review verification |
| 0019 | [WhatsApp outbound delivery and controls](0019-whatsapp-delivery-and-controls.md) | 0013–0018; WhatsApp WA-05 |
| 0020 | [WhatsApp delivery review](0020-whatsapp-delivery-review.md) | 0019 |
| 0021 | [WhatsApp pilot packaging](0021-whatsapp-pilot-packaging.md) | 0020; WhatsApp WA-06 |
| PR 148 | [Atomic run leg accounting contract](pr-0148-run-leg-accounting-contract.md) | issue 140 review follow-up |
| PR 148 | [Controls while sending is paused](pr-0148-paused-channel-controls.md) | WhatsApp composition review follow-up |
| Issue 151 | [Protected root access](issue-0151-protected-root-access.md) | 0006 security follow-up |

Before starting any task, read `AGENTS.md` and the skill(s) the task lists.
