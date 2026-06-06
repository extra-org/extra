# ADR 0002: YAML is compiled, not executed directly

- **Status:** Accepted
- **Date:** Foundation phase
- **Related:** [YAML_SPEC.md](../YAML_SPEC.md),
  [ARCHITECTURE.md](../ARCHITECTURE.md)

## Context

The agent system is described in YAML. We must decide how the runtime consumes
that YAML. One option is to load it into dictionaries and have the runtime read
those dictionaries directly during execution. Another is to validate and compile
the YAML into typed internal models first.

## Decision

YAML is a **declarative specification**, not executable business logic. The
pipeline is strictly:

```
config.yml → validate → compile → CompiledAgentGraph → runtime
```

- The YAML is **validated** first (schema, required fields, referential
  integrity, graph well-formedness).
- The validated spec is **compiled** into typed, immutable internal models.
- The runtime operates **only** on the compiled models. It **never** reads or
  executes raw YAML dictionaries directly.

## Consequences

**Positive**

- Errors are caught early, at validation/compile time, with clear messages —
  not deep inside request handling.
- The runtime works with typed objects, enabling static typing and refactoring
  safety.
- A clear boundary prevents YAML from creeping into a pseudo programming
  language: it can only express what the schema and compiler allow.

**Negative / constraints**

- Requires maintaining schema models, a validator, and a compiler (tasks 0002,
  0003).
- Any new YAML capability must be added to the schema and compiler, not handled
  ad hoc at runtime.

## Alternatives considered

- **Interpret raw dicts at runtime:** fast to start, but pushes errors to
  request time, prevents static typing, and tempts contributors to add
  executable behavior into YAML. Rejected.

## Enforcement

- The runtime layer imports compiled model types, never raw spec dicts.
- Routing/condition metadata in YAML is declarative; it is never `eval`'d or
  executed as code.
- Validation must pass before compilation; compilation must succeed before the
  runtime is constructed.
