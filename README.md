<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/assets/logo-dark.svg">
    <img src=".github/assets/logo-light.svg" alt="Extra" height="60">
  </picture>
</p>

<h1 align="center">Turn your product into an AI-powered assistant.</h1>

<p align="center">
  Give your users an AI-powered way to use your product—with zero backend rewrites.
</p>

<p align="center">
  <a href="https://docs.extra-ai.co/docs/introduction"><img alt="Docs" src="https://img.shields.io/badge/docs-available-blue"></a>
  <img alt="Version" src="https://img.shields.io/badge/version-beta-orange">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-green"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#why-extra">Why Extra</a> ·
  <a href="https://docs.extra-ai.co/docs/introduction">Documentation</a> ·
  <a href="#contributing">Contributing</a>
</p>

---

## Why Extra

Extra gives your customers an AI-powered way to use your product.

It works with the APIs, business logic, and workflows you already have — without requiring you to redesign your product around AI.


* **No backend rewrite.** Keep your existing APIs, services, and business logic as they are.

* **Specialized by design.** Each AI specialist owns a specific part of your business.

* **Your backend stays in control.** Business logic, data, credentials, and authorization remain in trusted code.

* **Explicit orchestration.** Work moves between specialists through predictable and inspectable execution paths.

* **Built for your product.** Expose Extra through an API or embed the assistant directly into your application.

**Not just a chatbot.** Extra doesn't stop at answering questions. It can execute real product workflows using your existing APIs and tools.


## Quick Start

You need Docker and a language model.

Use a supported cloud provider with an API key, or run open-source models locally with Ollama.

Create `agents.yml` — an orchestrator that routes to two focused agents:

```yaml
system:
  name: "Support Assistant"

defaults:
  model:
    provider: anthropic
    name: claude-sonnet-4-6

orchestrators:
  support_router:
    description: "Routes each request to the agent that owns it."
    prompts:
      orchestrator: prompts/support_router/orchestrator.md

agents:
  orders_agent:
    description: "Handles order status, shipping changes, and returns."
    prompts:
      system: prompts/orders_agent/system.md

  billing_agent:
    description: "Handles invoices, subscriptions, and refunds."
    prompts:
      system: prompts/billing_agent/system.md

# Indentation is the hierarchy: the orchestrator routes to both agents.
graph:
  support_router:
    orders_agent:
    billing_agent:
```

Scaffold the prompt and plugin stubs the YAML references. It never overwrites a
file you already wrote:

```bash
docker run --rm -v "$(pwd):/workspace" -w /workspace \
  ghcr.io/extra-org/extra:latest generate --config agents.yml
```

Fill in the three prompt stubs it created:

```markdown
<!-- prompts/support_router/orchestrator.md -->
Route orders, shipping, and returns to orders_agent.
Route invoices, plans, and refunds to billing_agent.

<!-- prompts/orders_agent/system.md -->
Handle order status, shipping changes, and returns using the available tools.

<!-- prompts/billing_agent/system.md -->
Handle invoices, subscriptions, and refunds using the available tools.
```

Run it with Agent Manager, which serves the conversation API, history, and the
chat widget:

```bash
docker run -p 8100:8100 -v "$(pwd):/workspace" -w /workspace \
  -e ANTHROPIC_API_KEY=sk-... \
  ghcr.io/extra-org/extra:latest \
  agent-manager --config agents.yml --port 8100
```

Talk to it in the browser at **http://localhost:8100/playground**, or over the
API — create a conversation with an id you choose, then send it a message:

```bash
curl -X POST http://localhost:8100/conversations \
  -H "Content-Type: application/json" \
  -d '{"session_id":"readme-demo"}'

curl -X POST http://localhost:8100/conversations/readme-demo/messages \
  -H "Content-Type: application/json" \
  -d '{"message":"Tell me about my system"}'
```

Tools, MCP servers, deeper routing, per-node authorization, and embedding the
chat widget are covered in the
[Quickstart](https://docs.extra-ai.co/docs/quickstart).

## Features

- AI specialists
- Workflow orchestration
- Authorization outside the LLM
- Local tools and MCP
- Human approvals
- Streaming API
- Embeddable chat widget
- Anthropic, OpenAI, Gemini, and Bedrock
- Langfuse tracing

## Architecture

Extra executes an explicit orchestration graph.

Orchestrators route requests to AI specialists. Each specialist owns its own prompts, tools, MCP servers, and authorization.

Your business logic stays in your backend. Extra only orchestrates execution.

```mermaid
flowchart TD
    U([User request]) --> R{{Orchestrator}}

    R --> A1[Billing specialist]
    R --> A2[Orders specialist]
    R --> A3[Docs specialist]

    A1 --> T1[Business logic / APIs]
    A2 --> T2[Business logic / APIs]
    A3 --> T3[Business logic / APIs]

    T1 --> RESP([Response])
    T2 --> RESP
    T3 --> RESP
```

Extra runs the graph. Your project's plugins hold the trusted business logic —
tools, access checks, and the values resolved into prompts.

- **[Tutorial](https://docs.extra-ai.co/docs/tutorial)** — build a complete multi-agent system step by step.
- **[YAML reference](https://docs.extra-ai.co/docs/yaml-spec)** — every field you can declare.
- **[Architecture](https://docs.extra-ai.co/docs/architecture)** — how routing and execution work.
- **[`examples/`](examples/)** — runnable specs, including an enterprise knowledge assistant.


## Contributing

This repository is **agent-first** — if you're an AI coding agent, read
[AGENTS.md](AGENTS.md) before making changes. Human contributors should
start there too, then run `make check` before opening a PR.

## License

[MIT](LICENSE)
