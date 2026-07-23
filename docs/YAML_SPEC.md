# YAML Specification

This document describes the declarative Agent Engine configuration. The schema
source for the current example is [`examples/config.schema.json`](../examples/config.schema.json),
and the reference sample is [`examples/enterprise-knowledge-assistant/agents.yaml`](../examples/enterprise-knowledge-assistant/agents.yaml).
Validation, schema models, compilation, and runtime execution are implemented.

This JSON Schema is a reference artifact for editor tooling — it is not loaded
by the engine at runtime (the real validation logic lives in
`src/agent_engine/core/validator.py`). To get autocomplete/validation in your
editor (e.g. VS Code with the YAML extension), add this line to the top of
your spec file:

```yaml
# yaml-language-server: $schema=path/to/examples/config.schema.json
```

The YAML has two conceptual halves:

- **Flat declarations** describe what exists: MCP servers, plugin tools,
  resolvers, orchestrators, and executor agents.
- **`graph` topology** describes how those declared nodes are connected at
  runtime. Indentation is the topology.

The runtime never executes raw YAML. The platform validates this file into typed
spec models first. Later tasks compile validated specs into a typed graph and
run requests against that graph.

---

## Conceptual Shape

```yaml
system:
  name: "GlobalCorp AI System"

defaults:
  model:
    provider: anthropic
    name: claude-sonnet-4-6
    temperature: 0.7

mcps:
  flights_mcp:
    url: "https://company.com/mcp/flights"

tools:
  book_flight:
    description: "Search and book a flight given origin, destination and travel date"

resolvers:
  current_date:
    scope: shared

orchestrators:
  main_router:
    description: "Routes the user by topic."
    prompts:
      orchestrator: "prompts/main_router/orchestrator.md"
      system: "prompts/main_router/system.md"

agents:
  domestic_flights_agent:
    description: "Search and book flights within the country."
    prompts:
      system: "prompts/domestic_flights/system.md"
      user: "prompts/domestic_flights/user.md"
    resolvers: [current_date]
    tools: [book_flight]
    mcps: [flights_mcp]

graph:
  main_router:
    domestic_flights_agent:
```

---

## Top-Level Keys

| Key             | Required | Purpose |
| --------------- | -------- | ------- |
| `system`        | yes      | Human-readable system metadata. |
| `defaults`      | no       | System-wide defaults, currently `defaults.model`. |
| `mcps`          | no       | URL-based MCP server declarations keyed by id. |
| `tools`         | no       | Python plugin tools exposed to LLM agents. |
| `resolvers`     | no       | Deterministic prompt-variable resolvers. |
| `orchestrators` | no       | Router nodes. |
| `agents`        | no       | Executor nodes. |
| `graph`         | yes      | Runtime topology, with one root entrypoint. |
| `hooks`         | no       | Trusted runtime hooks (auth/policy/audit). See [RUNTIME_HOOKS.md](RUNTIME_HOOKS.md). |
| `plugins`       | no       | Plugin loading config. `plugins.import_roots` lists dirs (resolved relative to this file) to put on `sys.path` so package-path plugin refs import reliably. See [RUNTIME_HOOKS.md](RUNTIME_HOOKS.md). |

Secrets must not appear in YAML. Hooks are **not** tools — they are never
exposed to the LLM; see [RUNTIME_HOOKS.md](RUNTIME_HOOKS.md).

Hook entries support two forms. Generated/client-owned hooks should use logical
plugin ids resolved through `plugins/plugins.toml`:

```yaml
hooks:
  before_mcp_request:
    - plugin: "mcp_auth"
      method: "before_mcp_request"
```

Authentication hooks should read environment variables or secret-manager values
inside plugin code, then add whatever headers or credentials are needed at
runtime. YAML must not contain secrets or framework-specific credential
shortcuts.

Advanced/manual integrations may still use explicit import refs:

```yaml
hooks:
  before_mcp_request:
    - ref: "company.plugins.auth:add_headers"
```

An entry must use either `ref` or `plugin` + `method`, not both.

---

## Node Types

There are two declared node types. They are separate for developer clarity even
if the compiler stores them in one internal node model.

**Orchestrators** are routers. They choose among their children using the
children's `description` fields plus the orchestrator prompt. They do not own
tools or MCP servers; their children are their capabilities.

```yaml
orchestrators:
  flights_router:
    name: "Flights Router"
    description: "Routes flight requests to domestic or international handling."
    model:
      provider: anthropic
      name: claude-haiku-4-5
      temperature: 0.0
    prompts:
      orchestrator: "prompts/flights_router/orchestrator.md"
      system: "prompts/flights_router/system.md"
```

**Agents** are executors. They run an LLM with prompt files and may call tools
or MCP servers.

```yaml
agents:
  super_agent:
    name: "Supermarket"
    description: "Handle supermarket orders and cart operations."
    prompts:
      system: "prompts/super/system.md"
    resolvers: [user_name, subscription]
    tools: [add_to_cart]
    mcps: [super_mcp]
```

Every id referenced in `graph`, `resolvers`, `tools`, or `mcps` must be declared
in the corresponding top-level section.

### `auto` (optional, agent-level)

`auto` is an optional boolean on an **agent** (default `false`). It controls
Human-in-the-Loop (HITL) behavior for that agent's tool calls:

```yaml
agents:
  invoices_agent:
    description: "Handles invoices and sends confirmations."
    tools: [send_invoice_email]
    auto: true      # execute this agent's tools without asking for approval
```

- `auto: false` (or omitted) — **every** tool call requires explicit human
  approval before it runs, unless the same tool was already approved for the
  current session (see [HUMAN_IN_THE_LOOP.md](HUMAN_IN_THE_LOOP.md)). There is no
  risk classification: approval is the default for all tools.
- `auto: true` — the agent runs without approval interrupts: its tool calls
  execute immediately and the approval provider is never invoked. This is
  evaluated per agent, so it never enables automatic execution for another agent
  in the same run.

> `auto_mode` is accepted as a backward-compatible alias for `auto`.

There is no per-tool approval configuration in YAML; the only HITL knob is this
agent-level flag. See [HUMAN_IN_THE_LOOP.md](HUMAN_IN_THE_LOOP.md) for the full
approval, session, checkpointing, and resume model.

MCP declarations are URL-based:

```yaml
mcps:
  flights_mcp:
    url: "https://company.com/mcp/flights"
```

The platform creates a remote MCP client for each configured URL during engine
`build()` (via `langchain-mcp-adapters`). Users do not implement MCP client
classes, and stdio/local process MCP servers are not part of the current YAML
contract.

Each server may also declare an optional, per-server **`tool_tags`** discovery
selector for tag-aware servers. It changes nothing when absent. For the common
case you only list the tags — they are sent by default as the header
`X-MCP-Tool-Tag` (comma-joined). Filtering is server-side, and only the final
discovered tools are bound (never exposed to the LLM as tags). See
[MCP_AND_TOOLS.md](MCP_AND_TOOLS.md) → "Optional tool-discovery tags".

```yaml
mcps:
  # Simple, recommended: tags only -> sent as X-MCP-Tool-Tag: policies
  docs_platform:
    url: "https://mcp.company.com/mcp"
    tool_tags:
      - "policies"
```

`tool_tag_transport` is an optional advanced override (custom header or a
`query_param`); when present its `type` must be `header` (with `header_name`) or
`query_param` (with `param_name`), else parsing fails clearly:

```yaml
mcps:
  partner_docs_platform:
    url: "https://mcp.company.com/mcp"
    tool_tags: ["policies", "architecture"]
    tool_tag_transport: { type: query_param, param_name: "tag" }
```

---

## Prompts

Prompt text lives in files, not inline YAML. Each node uses a `prompts:` object:

| Field | Applies to | Required |
| ----- | ---------- | -------- |
| `orchestrator` | orchestrators only | yes |
| `system` | orchestrators and agents | no |
| `user` | orchestrators and agents | no |

All prompt files may contain `{{variables}}`. Variables are resolved per request
by declared resolvers before the node runs. Parsed templates may be cached;
rendered prompts are never cached globally.

Recommended layout:

```text
prompts/
  main_router/
    orchestrator.md
    system.md
  domestic_flights/
    system.md
    user.md
```

---

## Resolvers vs. Tools

Resolvers and tools both point at Python plugin methods, but they run at
different times and have different trust boundaries.

| | Resolver | Tool |
| --- | --- | --- |
| Runs | Before the node runs | During LLM execution |
| Chosen by | Engine | LLM |
| Exposed to LLM | No | Yes |
| Token cost | None | Yes |
| Purpose | Fill `{{variables}}` | Perform actions |

Resolvers are declared as ids in YAML. Each resolver has a **scope**:

- `shared` — generated once on `SharedResolver`; inherited by all agents.
- `agent` (default) — generated only on the declaring agent's resolver subclass.

```yaml
resolvers:
  current_date:
    scope: shared
  subscription:
    scope: agent
```

Resolvers are generated one file per agent under `plugins/resolvers/`: each
agent file defines a `Resolver` class, and shared methods live on a
`SharedResolver` in `plugins/resolvers/shared.py` that agent classes inherit.
The runtime loads the agent's `Resolver` by file path and instantiates it once.

The importable refs are catalogued in the single plugin manifest
`plugins/plugins.toml` (see [RUNTIME_HOOKS.md](RUNTIME_HOOKS.md) →
"The `plugins.toml` manifest"). The runtime reads only `[hooks.plugins]` to
resolve managed hook plugin ids; resolver/tool entries remain documentation and
generation metadata:

```toml
[resolvers]
shared = "plugins.resolvers.shared:SharedResolver"
domestic_flights_agent = "plugins.resolvers.documentation_agent:Resolver"
```

Methods receive `ctx`, which the engine builds from request headers and request
data. Shared methods are inherited through normal Python inheritance;
agent-scoped methods live only on the relevant agent class.

Run `agentctl generate` to create resolver stubs. See
[`SIDECAR_CONTEXT_AUTH.md`](SIDECAR_CONTEXT_AUTH.md) for the full resolver
plugin contract including generation modes and overwrite protection.

---

## Model Configuration

`defaults.model` sets the default model for every node. A node may define its own
`model`, but a node-level model is a **full replacement**, not a field-level
merge. If a node overrides the model, it must provide all required model fields.

Anthropic:

```yaml
model:
  provider: anthropic
  name: claude-haiku-4-5
  temperature: 0.0
```

Amazon Bedrock for Anthropic Claude models:

```yaml
model:
  provider: bedrock
  name: anthropic.claude-3-5-haiku-20241022-v1:0
  region: us-east-1
  temperature: 0.0
```

For Bedrock, `region` may be omitted from YAML when `AWS_REGION` or
`AWS_DEFAULT_REGION` is set. AWS credentials are resolved by the normal AWS
credential chain, such as `AWS_PROFILE`, `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN`, `~/.aws/credentials`, SSO, or an
IAM role. Secrets must never be stored in YAML.

Google Gemini:

```yaml
model:
  provider: gemini
  name: gemini-2.5-flash
  temperature: 0.2
```

For Gemini, set `GEMINI_API_KEY` in your environment and install the provider
extra with `pip install "agent-engine[gemini]"`. Any Gemini model your key can
access may be used via `name`. `max_tokens` maps to Gemini's `max_output_tokens`.
Secrets must never be stored in YAML.

OpenAI:

```yaml
model:
  provider: openai
  name: gpt-4.1-mini
  temperature: 0.2
```

For OpenAI, set `OPENAI_API_KEY` in your environment and install the provider
extra with `pip install "agent-engine[openai]"`. Any OpenAI model your key can
access may be used via `name`. Secrets must never be stored in YAML.

### Any OpenAI-compatible endpoint

The `openai` provider is not limited to `api.openai.com`. A handful of
well-known OpenAI-compatible vendors are wired in as `provider` shorthand, so
`base_url` and `api_key_env` resolve automatically:

```yaml
model:
  provider: zai
  name: glm-5.2
  temperature: 0.2
```

| `provider` | Vendor     | `base_url`                              | `api_key_env`       |
| ---------- | ---------- | ---------------------------------------- | -------------------- |
| `zai`        | Z.AI (GLM) | `https://api.z.ai/api/coding/paas/v4`     | `ZAI_API_KEY`         |
| `deepseek`   | DeepSeek   | `https://api.deepseek.com/v1`             | `DEEPSEEK_API_KEY`    |
| `moonshot`   | Moonshot   | `https://api.moonshot.ai/v1`              | `MOONSHOT_API_KEY`    |
| `groq`       | Groq       | `https://api.groq.com/openai/v1`          | `GROQ_API_KEY`        |
| `xai`        | xAI (Grok) | `https://api.x.ai/v1`                     | `XAI_API_KEY`         |
| `openrouter` | OpenRouter | `https://openrouter.ai/api/v1`            | `OPENROUTER_API_KEY`  |

`name` must be a model ID that vendor's endpoint actually serves (`glm-5.2`
for Z.AI, `deepseek-chat` for DeepSeek, and so on); it is passed through
unmodified, no provider picks a default model on your behalf.

For anything not in the table (a different vendor, a self-hosted Ollama or
vLLM server, a proxy in front of one of the listed vendors), stay on
`provider: openai` and set `base_url` and `api_key_env` directly:

```yaml
model:
  provider: openai
  name: llama3.1
  base_url: http://localhost:11434/v1
  api_key_env: OLLAMA_API_KEY
```

`api_key_env` names the environment variable holding the key, it is never
the key itself, and secrets must never be stored in YAML. If a local server
doesn't check the key at all, point `api_key_env` at any variable set to a
placeholder value; the field is still required so the request always sends
an `Authorization` header.

`base_url`/`api_key_env` also override a listed vendor's preset when both a
`provider` shorthand and one of these fields are set, useful for routing a
known vendor through an internal proxy without giving up the shorthand:

```yaml
model:
  provider: zai
  name: glm-5.2
  base_url: https://llm-proxy.internal.example.com/zai
  api_key_env: INTERNAL_PROXY_KEY
```

---

## Graph Topology

`graph` is a nested mapping. The single top-level key is the root entrypoint.
Every key must reference an id declared under `orchestrators` or `agents`.

```yaml
graph:
  main_router:
    flights_router:
      domestic_flights_agent:
      international_flights_agent:
    super_agent:
    admin_agent:
```

A `null` value means leaf node. YAML also represents an empty mapping key like
`super_agent:` as `null`, so leaves may be written naturally.

Semantic validation must enforce:

- exactly one root entrypoint;
- every graph id exists in `orchestrators` or `agents`;
- orchestrators may have children;
- agents are normally leaves for the MVP;
- repeated ids are allowed to model DAG reachability, but the compiler must
  assign stable node paths for each occurrence;
- cycles are rejected.

---

## Access Control

Authorization is opt-in per node:

```yaml
agents:
  admin_agent:
    description: "Sensitive administrative operations."
    protected: true
    prompts:
      system: "prompts/admin/system.md"
```

If any node has `protected: true`, the engine expects a fixed plugin contract at
`plugins/access.py`:

```python
class AccessResolver:
    def can_access(self, ctx, node_id: str) -> bool:
        ...
```

Before routing, protected nodes are checked. Allowed nodes remain candidates;
denied nodes are hidden from the router entirely. Non-protected nodes are never
checked. Access failures fail closed. `protected: true` without the access plugin
is a configuration error at startup.

---

## Request Shape

The engine is stateless with respect to conversation. An upstream application
owns sessions and memory, then invokes the engine with a complete conversation:

```http
POST /api/invoke
Authorization: Bearer <token>
Content-Type: application/json

{
  "messages": [{ "role": "user", "content": "Compare LangGraph and Temporal for building a research agent." }]
}
```

The engine passes headers and request data into `ctx`; client plugin code
interprets auth tokens and business identity.

---

## Validator Rules For Task 0002

- Parse YAML safely; never execute YAML content.
- Reject unknown keys according to `examples/config.schema.json`.
- Enforce one root in `graph`.
- Enforce graph references to declared node ids.
- Enforce resolver/tool/MCP references.
- Enforce orchestrator `prompts.orchestrator`.
- Enforce full-replacement model overrides.
- Enforce access plugin requirements when `protected: true` exists.
- Reject literal secrets in config and prompt paths.
- Collect all validation errors with useful locations.
