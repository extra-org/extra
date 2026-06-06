# Claude Code Workflow

How this repository is prepared for **Claude Code**, and how to work in it
effectively. This complements `AGENTS.md` (the tool-agnostic manual) and
`CLAUDE.md` (the Claude entrypoint).

---

## 1. How this repository is prepared for Claude Code

The repo ships Claude-native configuration so Claude Code has a clear entrypoint,
reusable skills, and focused subagents:

```
CLAUDE.md                      ← Claude entrypoint (mission, rules, skill/agent map)
.claude/
├── settings.json              ← conservative, shared project settings
├── skills/<name>/SKILL.md     ← Claude-native skill invocation layer
└── agents/<name>.md           ← Claude subagent definitions
docs/CLAUDE_CODE_WORKFLOW.md   ← this file
skills/                        ← root, tool-agnostic playbook library (deeper)
```

## 2. What `CLAUDE.md` is for

`CLAUDE.md` is the project entrypoint Claude Code reads first. It states the
mission, the core pipeline, the non-negotiable architecture rules, what to read
before feature work, how to use skills and subagents, the validation commands,
and the required final-response format. If `CLAUDE.md` and `AGENTS.md` ever
disagree, `AGENTS.md` wins.

## 3. What `.claude/skills/` is for

`.claude/skills/<name>/SKILL.md` is the **Claude-native invocation layer**: short,
operational skills with frontmatter (`name`, `description`) that Claude can
select per task. Each one is concise and **references the deeper root skill**
(e.g. "also read `skills/testing-skill.md`") instead of duplicating it.

Available: `code-review`, `testing`, `senior-python-engineering`,
`architecture-review`, `refactoring`, `documentation`, `yaml-schema`,
`runtime-engine`, `prompt-rendering`, `sidecar-auth-context`, `mcp-tools`.

## 4. What `.claude/agents/` is for

`.claude/agents/<name>.md` defines **subagents** — focused personas with their
own instructions and (where useful) restricted tools:

- **architect** — architecture planning/review (read-only; doesn't implement
  unless asked).
- **code-reviewer** — senior structured review (read-only).
- **test-engineer** — plans/writes pytest tests; never calls real services.
- **documentation-writer** — updates docs/ADRs honestly.

## 5. Root `skills/` vs. `.claude/skills/`

| | Root `skills/` | `.claude/skills/` |
| --- | --- | --- |
| Audience | Any tool/agent | Claude Code |
| Depth | In-depth playbooks | Concise, operational |
| Role | Source of truth for *how* | Invocation layer that points to the root skill |
| Format | Project playbook sections | Frontmatter + standard SKILL sections |

Rule of thumb: **start from the `.claude/skill`, then read the referenced root
skill** for full detail before doing the work. Don't duplicate content between
them — improve the root skill and let the Claude skill point to it.

## 6. Recommended workflow

1. **Read the task** in `tasks/` (Goal, Scope, Files allowed to change, Out of
   scope).
2. **Read relevant docs** (`AGENTS.md`, `CLAUDE.md`, and the architecture/layer
   docs the task touches).
3. **Use the relevant skill(s)** — the matching `.claude/skills/*` and its root
   skill. If the task touches multiple areas, read all relevant skills first.
4. **Plan** the change in small, task-sized steps (consider the `architect`
   subagent for design-heavy work).
5. **Implement a small change** within scope.
6. **Run validation:** `make check` (format-check + lint + test).
7. **Report clearly** using the final-response format in `CLAUDE.md`/`AGENTS.md`.

## 7. What not to do

- **Do not build everything in one task.** Work task-by-task.
- **Do not rewrite architecture casually.** Contract/architecture changes need an
  ADR (use the architecture-review skill).
- **Do not skip tests.** Behavior ships with tests; mock external systems.
- **Do not hardcode secrets** in code, YAML, prompts, or `.claude/` config.
- **Do not duplicate** large docs/skills — reference them.
