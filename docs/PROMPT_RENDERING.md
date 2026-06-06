# Prompt Rendering

Prompt rendering is implemented in task `0005`. This document describes the
current design contract.

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

`orchestrator` prompts contain routing instructions. `system` prompts describe
node behavior/persona. `user` prompts can wrap the incoming user input.

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
    class: Resolvers
    method: current_date
  user_name:
    class: Resolvers
    method: user_name

agents:
  domestic_flights_agent:
    description: "Search and book flights within the country."
    prompts:
      system: "prompts/domestic_flights/system.md"
    resolvers: [current_date, user_name]
```

Resolver methods receive `ctx` and return values before the node runs. Resolvers
are not exposed to the LLM and do not consume tokens.

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
