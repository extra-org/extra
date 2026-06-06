# Task 0003 — Compiled Agent Graph

## Goal

Compile a validated spec into an immutable, typed `CompiledAgentGraph`: resolve
references, normalize flat declarations, expand `graph` topology, and link nodes
to prompts, resolvers, tools, MCP servers, and model configuration. **No runtime
execution.**

## Context

The runtime (0004) operates only on compiled, typed models — never raw YAML.
This task bridges validated YAML to that model.

**Read first:** `AGENTS.md`, `.ai/skills/runtime-engine.md`,
`docs/ARCHITECTURE.md` (compiler + agent graph layers), `docs/YAML_SPEC.md`,
`docs/adr/0002-yaml-is-compiled-not-executed-directly.md`.

## Scope

- Define `CompiledAgentGraph` and related typed models.
- Represent orchestrators and agents as node definitions with a clear node type.
- Expand the nested `graph` mapping into traversable compiled node instances.
- Resolve prompt, resolver, tool, MCP, and model bindings.

## Files allowed to change

- `src/agentplatform/graph/**`
- `src/agentplatform/compiler/**`
- `tests/graph/**`, `tests/compiler/**`

## Requirements

- The compiler accepts only the validated spec from task 0002.
- Resolve all id references into direct typed links.
- Root instance comes from the single top-level `graph` key.
- Each graph occurrence gets a stable `instance_id`, `node_id`,
  `parent_instance_id`, and path.
- The same node id may appear in multiple graph locations; each occurrence
  becomes a distinct compiled instance pointing to the same node definition.
- The resulting graph is immutable.
- Model config is resolved with default inheritance and full-replacement node
  overrides.
- Prompt/resolver/tool/MCP bindings are available from each compiled instance.
- Provide a single public entry point, e.g.
  `compile_spec(validated_spec) -> CompiledAgentGraph`.

## Out of scope

- `RuntimeEngine` / `ExecutionContext` / execution (task 0004).
- Prompt rendering, plugin invocation, MCP calls.
- Reading raw YAML.

## Acceptance criteria

- [ ] `CompiledAgentGraph` is typed and immutable.
- [ ] Orchestrator and agent node definitions are distinct or clearly typed.
- [ ] Graph topology expands into traversable instances from the root.
- [ ] Reused node ids produce multiple distinct instances.
- [ ] All id references are resolved into direct links.
- [ ] Effective model config is available per instance.
- [ ] The compiler input is the validated spec, not raw YAML.
- [ ] Tests cover `examples/agents.yml`, reused node ids, and graph traversal.
- [ ] `make check` passes.

## Commands to run before finishing

```bash
make check
```

## Expected final report

Use the AGENTS.md §9 format. Confirm the graph is immutable, references are
resolved, and the runtime will consume only compiled models. Recommend task 0004
next.
