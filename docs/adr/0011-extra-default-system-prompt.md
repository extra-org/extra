# ADR 0011 — Prepend an Extra default system prompt, opt-out per node

- **Status:** Proposed
- **Date:** 2026-11-10

## Context

Issue #100 asks Extra to ship a baseline system prompt that keeps every model
inside the capabilities an application configured: no invented tools, data or
actions; out-of-scope requests redirected; authorization, tool restrictions and
agent boundaries respected; no claims of actions that did not run. It must be
on by default, configurable from `agents.yaml`, combinable with each node's own
prompt in a deterministic order, and must not be duplicated during delegated
execution.

Before this change `AgentNode` and `OrchestratorNode` each built their system
prompt independently, so a shared fragment would have to be added in two
places. Agents and orchestrators already share `BasePromptSet` for their
`prompts:` block; the top-level `system:` block holds only metadata.

## Decision

- **YAML flag.** `prompts.extra_default` is a boolean on both agents and
  orchestrators (`BasePromptSet.extra_default`). It lives under `prompts:`
  because that is where a node's prompt configuration already lives; no new
  top-level or inline-prompt mechanism is introduced. The parser rejects
  non-boolean values, and `examples/config.schema.json` declares the same
  property so editor/schema validation agrees with the engine.
- **Enabled by default.** The flag defaults to `true`, so existing
  configurations receive the default prompt without changes.
- **Composition order.** One generic helper, `assemble_prompt(*fragments)`,
  composes every node's final system prompt from, in order:
  1. `EXTRA_DEFAULT_SYSTEM_PROMPT`, unless the node opts out;
  2. the node's own prompt — `prompts.system` (or `description` as fallback)
     and, for orchestrators, `prompts.orchestrator` — rendered with resolver
     values;
  3. for orchestrators only, the engine routing contract.

  Fragments are joined with a blank line; `None` and whitespace-only fragments
  are skipped. Only fragment 2 is interpolated: the default and the contract
  are engine text.
- **Custom text is preserved.** `assemble_prompt` never trims a non-blank
  fragment. Leading indentation and trailing newlines in an application's
  prompt files reach the model byte-for-byte, because whitespace can carry
  meaning (for example a Markdown code block marking text as an example rather
  than an instruction). Only engine-owned text such as the routing contract may
  be normalized.
- **Opt-out semantics.** `extra_default: false` removes only Extra's default
  fragment from that node. The node's own prompt and, for orchestrators, the
  routing contract are unchanged. The setting is per node: a parent's choice
  does not affect its children, or vice versa.
- **No duplication.** Each node's model call carries its own complete system
  prompt; delegation passes a child only the user message, not the parent's
  system prompt. The default therefore appears exactly once in every model call
  of an enabled node.

## Contract changes

- YAML: new optional boolean `prompts.extra_default` (default `true`) on agents
  and orchestrators; declared in the published JSON Schema.
- Model-facing text: every enabled node's system prompt now begins with the
  Extra default. Orchestrator prompts no longer contain the extra blank lines
  previously produced around an empty `orchestrator` file or the contract's
  leading newline.
- `BasePromptSet` gains the `extra_default` field (defaulted, so existing
  constructors remain source compatible).

There is no HTTP API, persistence, or widget contract change.

## Consequences

- Applications get scope and honesty guardrails without writing them, and can
  opt individual nodes out without losing their own prompts.
- The default is a prompt-level request, not enforcement. Access control,
  tool restrictions and approvals remain enforced in code (access plugin,
  hooks, HITL); the default prompt does not replace them.
- Stateless utility calls outside the agent graph, such as conversation title
  generation via `engine.complete()`, do not receive the default.
- The default text is fixed engine content; it is not overridable beyond the
  per-node opt-out. Changing its wording changes model-facing behavior for
  every enabled node and should be treated as a contract change.
