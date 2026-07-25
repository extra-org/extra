# Starter example — Bank Concierge

The example to read first. A small bank assistant that uses every part of the
platform, with logic simple enough that nothing distracts from the wiring.

Everything is fake — two accounts, five transactions, no database, no internet.
One API key is the only thing you need.

## Run it

From the repository root:

```bash
make up EXAMPLE=examples/starter
```

First run creates `.env` from `.env.example` and stops so you can add your key.
Then:

```bash
agentctl chat --url http://localhost:8090
```

Or without Docker — two terminals:

```bash
cd examples/starter && python3 -m mcps.bank.server
```
```bash
agentctl chat --config examples/starter/agents.yaml
```

Offline checks need no key and no MCP:

```bash
make validate EXAMPLE=examples/starter
make inspect  EXAMPLE=examples/starter
```

## Try these

| Ask | What it shows |
| --- | --- |
| `What are your opening hours?` | routing three levels deep; a resolver filling `{{bank_name}}` |
| `How much money do I have?` | an MCP tool call against the bundled server |
| `How much did I spend on groceries and coffee?` | the model reading tool output and computing an answer |
| `I lost my card ending 4821, please block it` | a local Python tool — and an approval prompt before it runs |

The route is printed with every answer, so you can see which agents handled it.

## The system

```
concierge                    routes to a department, never answers
├── support_router
│   ├── faq_agent            auto: true — no tools, answers from its prompt
│   └── card_agent           block_card local tool, requires approval
└── accounts_router
    ├── balance_agent        get_accounts      (bank_core MCP)
    └── transactions_agent   get_transactions  (bank_core MCP)
```

Orchestrators route and never answer. Agents do the work. A node can only reach
its own children — the indentation in `graph:` *is* the permission model.

## What each file is for

| Path | What it does |
| --- | --- |
| `agents.yaml` | the whole system: models, limits, tools, MCPs, resolvers, hooks, graph |
| `prompts/<node>/system.md` | the live prompt for that node |
| `prompts/<node>/orchestrator.md` | routing reference for humans; **not** loaded at runtime |
| `plugins/tools/block_card.py` | a local tool: one dict in, one string out |
| `plugins/resolvers/shared.py` | values injected into prompts as `{{name}}` |
| `plugins/resolvers/<agent>.py` | per-agent resolver class; inherits the shared one |
| `plugins/hooks/lifecycle.py` | one method per lifecycle point, each logging |
| `plugins/plugins.toml` | manifest of hooks, resolvers and tools |
| `mcps/bank/` | the bundled MCP server and its fake data |

Note the split in `prompts/`: the engine loads **`system.md`**. `orchestrator.md`
documents the routing policy for whoever reads the repo — keep the two in step.

## Switching model

Change `defaults.model` in `agents.yaml` and set the matching key in `.env`.
Nothing else references a provider, so it's one edit:

```yaml
defaults:
  model:
    provider: anthropic       # anthropic | openai | gemini | bedrock
    name: claude-haiku-4-5
    temperature: 0.0
```

A local OpenAI-compatible server works too — set `provider: openai`, the model
name Ollama reports, and in `.env`:

```
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://127.0.0.1:11434/v1
```

## Approvals

`card_agent` changes something, so its tool call pauses for a human decision.
The read-only agents set `auto: true` and run straight through.

Approvals are resolved by `agentctl chat` and by the engine's HTTP API
(`/runs/{run_id}/approvals/...`). `agentctl run` has no way to answer the
prompt, so a run that needs one returns nothing — use `chat` for the card.

## Adding to it

Add a node to `agents.yaml`, put it in `graph:`, then:

```bash
agentctl generate --config examples/starter/agents.yaml
```

That writes any missing prompt and plugin stubs without touching your existing
files, and does nothing when everything is already in place. Fill in the stubs
and commit them.
