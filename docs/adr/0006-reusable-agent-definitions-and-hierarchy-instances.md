# ADR 0006: Reusable node declarations and agent nodes

## Status

Accepted

## Context

The YAML carries two distinct concepts:

- A **node declaration** under `orchestrators` or `agents`, describing what the
  node is: type, description, prompts, model, resolvers, tools, MCPs, and
  protection.
- An **agent node**, which is one occurrence of that node id inside `graph`,
  describing where the node sits in the topology.

The same node id may appear in multiple graph locations to model DAG
reachability. Without distinct compiled agent nodes, routing and tracing become
ambiguous.

## Decision

Node declarations are reusable. Each occurrence inside `graph` compiles into a
distinct `AgentNode` that points back to the shared declaration.

The compiler produces, for each occurrence:

- `node_path`
- `node_id`
- node type (`orchestrator` or `agent`)
- `parent_node_path`
- resolved prompt/resolver/tool/MCP/model bindings

Runtime execution operates on compiled `AgentNode` objects, not raw
declarations or raw YAML mappings. Trace events use `node_path` as the primary
identity and also include `node_id`.

## Validation Rules

1. Every graph key must reference an existing orchestrator or agent declaration.
2. The graph has exactly one root.
3. Cycles are rejected in the MVP.
4. Repeated node ids are allowed, but each occurrence must compile to a distinct
   stable node path.
5. Runtime execution operates on `AgentNode` objects, not declarations.

## Consequences

- Nodes can be reused without duplicating YAML declarations.
- Tracing is unambiguous.
- Path-specific behavior remains possible later.
- The compiler owns graph expansion; the runtime never resolves graph structure
  from raw YAML.

## Alternatives Considered

1. **Treat graph ids as globally unique nodes.** Rejected because it prevents
   reuse.
2. **Share a single runtime identity for repeated occurrences.** Rejected
   because tracing/routing becomes ambiguous.
3. **Allow cycles/recursion in the MVP.** Rejected to keep execution bounded.

## Related

- [ADR 0001 — RuntimeEngine created once](0001-runtime-engine-created-once.md)
- [ADR 0002 — YAML is compiled, not executed directly](0002-yaml-is-compiled-not-executed-directly.md)
- Docs: [ARCHITECTURE.md](../ARCHITECTURE.md), [YAML_SPEC.md](../YAML_SPEC.md)
