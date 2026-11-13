# Prompt Rendering

Prompt rendering is partially implemented. Prompt file loading and `{{ variable }}`
substitution work inside the LangGraph builder. A dedicated `prompts/` module
with parsed-template caching and strict missing-variable errors is planned
(task `0005`). This document describes the full design contract.

Prompt text lives in files, not inline YAML. Nodes reference prompt files under a
`prompts:` object:

```yaml
orchestrators:
  main_router:
    description: "Routes the user by topic."
    prompts:
      orchestrator: "prompts/main_router/orchestrator.md"
      system: "prompts/main_router/system.md"

agents:
  super_agent:
    description: "Handle supermarket orders."
    prompts:
      system: "prompts/super/system.md"
      user: "prompts/super/user.md"
```

---

## Prompt Fields

| Field | Applies to | Required |
| ----- | ---------- | -------- |
| `orchestrator` | orchestrators only | yes |
| `system` | orchestrators and agents | no |
| `user` | orchestrators and agents | no |
| `extra_default` | orchestrators and agents | no (boolean, default `true`) |

`orchestrator` prompts contain routing instructions. `system` prompts describe
node behavior/persona. `user` prompts can wrap the incoming user input.

### Extra default system prompt

Every node's final system prompt is composed in a fixed order by
`assemble_prompt` (`agent_engine.engine.langgraph.prompting`), joining non-empty
parts with a blank line:

1. `EXTRA_DEFAULT_SYSTEM_PROMPT` — Extra's built-in scope/boundary prompt,
   unless the node sets `prompts.extra_default: false`.
2. The node's rendered prompt — `system` (or `description` as fallback), and
   for orchestrators, then `orchestrator`.
3. Orchestrators only: the engine routing contract.

Only part 2 is interpolated. `extra_default` is a per-node boolean (default
`true`); disabling it removes only the Extra default, never the node's own
prompt. Each node's model call carries its own complete system prompt, so the
default appears once per call during delegated execution. Non-blank prompt
text is never trimmed, so indentation in prompt files reaches the model as
written. See [ADR 0011](adr/0011-extra-default-system-prompt.md).

---

## Templates

Prompt files may contain variables:

```text
Hello {{user_name}}.
Today is {{current_date}}.
```

Variables are filled by resolver plugins declared in YAML and opted into by the
node:

```yaml
resolvers:
  current_date:
    scope: shared
  user_name:
    scope: shared

agents:
  domestic_flights_agent:
    description: "Search and book flights within the country."
    prompts:
      system: "prompts/domestic_flights/system.md"
    resolvers: [current_date, user_name]
```

Resolver classes are loaded by file path and catalogued in the single plugin
manifest `plugins/plugins.toml` (documentation/generation only). Resolver methods
receive `ctx` and return values before the node runs. Resolvers are not exposed
to the LLM and do not consume tokens.

---

## Startup vs. Per Request

At startup:

- load YAML;
- validate prompt file paths;
- optionally parse/cache prompt templates;
- build the long-lived runtime.

Per request:

- create `ExecutionContext`;
- build `ctx` from headers and request data;
- route to the next node;
- call that node's resolvers;
- render that node's prompt files;
- execute the orchestrator or agent;
- trace the result.

Parsed templates may be cached. Rendered prompt strings are request-specific and
must never be cached globally.

---

## Strict Rendering Rules

- Templates are data, not code.
- Unknown variables should fail clearly.
- Referenced resolvers must exist.
- Resolver errors should identify the node, resolver id, and variable.
- Secrets must not appear in YAML or prompt files.
- Prompt text is not a security boundary; access and data restrictions belong in
  access/tool/plugin logic.

---

## Prompt Provenance

To support reproducible agent runs and future remote prompt management integrations
(e.g., Langfuse), prompt source templates carry provider-agnostic provenance metadata
(`PromptProvenance`).

### Provenance Contract

```python
@dataclass(frozen=True)
class PromptProvenance:
    source: str
    identifier: str
    content_hash: str
    requested_ref: str | None = None
    resolved_revision: str | None = None
```

| Field | Description | Example (Local File) | Example (Remote / Langfuse) |
| ----- | ----------- | -------------------- | --------------------------- |
| `source` | Origin provider of the prompt | `"file"` | `"langfuse"` |
| `identifier` | Stable source identifier or path | `"prompts/billing/system.md"` | `"billing-agent"` |
| `content_hash` | Deterministic SHA-256 fingerprint (`sha256:<digest>`) of the source content | `"sha256:4a8b..."` | `"sha256:4a8b..."` |
| `requested_ref` | Requested label, tag, or version (if applicable) | `None` | `"production"` or `"12"` |
| `resolved_revision` | Exact immutable revision resolved by the source | `None` | `"17"` |

### Source Content vs. Rendered Content

- **Source content hash**: The fingerprint is computed deterministically over the raw source template text in UTF-8 before variable substitution (`compute_prompt_hash(content)`).
- **No sensitive leak**: Provenance tracks source templates, versions, and hashes rather than persisting sensitive request-specific rendered prompt content.
- **Local files**: Loaded via `load_file_with_provenance(base_dir, relative_path)` returning `ResolvedPrompt(content=..., provenance=...)` with normalized forward-slash paths.
