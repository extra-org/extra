# Prompt Rendering

This document defines how prompts become text. It is a design specification;
prompt rendering is implemented in task `0005`. Nothing here works yet.

→ See [ADR 0004](adr/0004-prompts-are-templates-rendered-per-request.md) and
[ADR 0005](adr/0005-prompt-rendering-and-context-resolution.md).

---

## Example template

A prompt file is a template containing placeholders, not finished text:

```text
You are the Invoice Reader Agent.

Customer code:
{{ customer_code }}

Current date:
{{ current_date }}
```

Values such as `customer_code`, `current_date`, `tenant_id`, permissions,
customer profile, `region`, and `locale` are **dynamic** — they can differ on
every request — so they are resolved and substituted **per request**, never baked
in at startup.

---

## Core rules

1. **Prompt files are templates.** They contain placeholders, not finished text.
2. **Dynamic values are resolved per request** from the `ExecutionContext`.
3. **Raw/parsed templates may be cached.** Parsing is deterministic and
   reusable, so cache the compiled template.
4. **Rendered prompts are never globally cached.** A rendered prompt contains
   request-specific (often tenant/identity-specific) data and must not be shared.
5. **Missing required variables fail clearly.** Rendering a template without a
   declared required variable raises a precise error — it never silently emits
   an empty string or `None`.
6. **Prompt injection is not security.** Enforcement happens at the tool/data
   layer, never via prompt wording. → See
   [SIDECAR_CONTEXT_AUTH.md](SIDECAR_CONTEXT_AUTH.md).

---

## Template vs. rendered prompt

| Concept          | Contains                         | Cacheable?            | Lifetime    |
| ---------------- | -------------------------------- | --------------------- | ----------- |
| Prompt template  | placeholders + static text       | ✅ yes (parsed form)  | application |
| Rendered prompt  | concrete values for one request  | ❌ never globally     | request     |

```
prompt file (template)  ──parse──►  cached template  ──render(context)──►  rendered prompt
        (on disk)                     (in memory)                            (per request)
```

---

## Startup vs. per-request

**At startup (once):**

- load YAML → validate → compile,
- validate prompt template **paths** (every referenced template exists),
- optionally load and **cache raw prompt templates**,
- create the `RuntimeEngine`.

**Per request:**

- create the `ExecutionContext`,
- authenticate/authorize if configured (see the sidecar),
- route to the selected agent,
- determine the **selected agent's required context**,
- **resolve dynamic context values** (via resolvers/sidecar),
- **render the prompt templates** with those values,
- execute the agent,
- record the trace.

Raw templates can be cached. Rendered prompts are **never** globally cached.

---

## Where context values come from

The YAML **declares the source**; the runtime **resolves it** through a
`ContextResolver`. Possible sources:

- the **sidecar** (`context` map from `/resolve-context`)
- a declared **resolver** (e.g. a generated resolver function/package — see
  [ARCHITECTURE.md](ARCHITECTURE.md#resolver-model))
- the **request** (body fields, query params, headers)
- **identity** (auth/JWT claims; resolved user/tenant/customer)
- **system time** (now, timezone)
- **memory** (conversation/session state)
- an **MCP tool** result
- a **database** lookup
- an external **API**
- a **plugin**

An agent declares which variables it requires (its `context` / `requires_context`
block); the resolver gathers them from the declared sources before rendering.
Client-specific resolution logic lives in the **resolver package, plugin, or
sidecar** — never in the core runtime.

```yaml
# declarative source mapping (illustrative)
agents:
  invoice_agent:
    prompt: prompts/invoice_agent.md
    requires_context:
      - tenant_id        # from sidecar/identity
      - customer_code    # from sidecar/identity
      - now              # from system time
```

The runtime resolves each declared variable, then renders the template. If a
required variable cannot be resolved, rendering fails with a clear error naming
the missing variable.

---

## Rendering behavior

- Use an explicit, sandboxed template mechanism — **templates are not code**.
  No arbitrary execution inside a prompt template.
- Unknown/undeclared variables in a template are an error (strict mode), not a
  silent blank.
- Output is plain text handed to the LLM provider. It carries **no** trust:
  any instruction a user can influence is treated as untrusted input.

---

## Caching policy (explicit)

- ✅ Cache the **parsed template** keyed by file path + version.
- ❌ Do **not** cache the **rendered** result across requests.
- ❌ Do **not** cache rendered results keyed by partial context (risk of leaking
  one request's data into another).

---

## Security: prompt text is not a boundary

A prompt that *instructs* the model — e.g. *"Only answer for customer
{{ customer_code }}"* — is **not** a security control. The model or a user can
ignore or subvert it. **Real enforcement is required at the tool/data layer**:
the runtime injects trusted values (e.g. `customer_code` from resolved identity)
and blocks user/LLM overrides via tool input policy. Treat all rendered prompt
content as untrusted input.
→ See [SIDECAR_CONTEXT_AUTH.md](SIDECAR_CONTEXT_AUTH.md),
[MCP_AND_TOOLS.md](MCP_AND_TOOLS.md), and the security/tool enforcement model in
[ARCHITECTURE.md](ARCHITECTURE.md#security--tool-enforcement-model).

---

## Validation checklist (for prompt changes)

- [ ] Templates are loaded/parsed once and cached; rendering happens per request.
- [ ] No rendered prompt is stored in a global/shared cache.
- [ ] Missing required variables raise a clear, named error.
- [ ] No executable logic is embedded in templates.
- [ ] No secrets are embedded in prompt files.
- [ ] Enforcement that matters for security lives in the tool/data layer, not the
      prompt text.
- [ ] `make check` passes.
