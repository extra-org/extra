# MCP server example

The smallest possible Extra system, exposed as an MCP server over stdio. It
exists to demonstrate that ``agentctl mcp serve`` works end-to-end:

1. an MCP client can discover the ``extra_chat`` tool,
2. the client can send a message and get an answer back,
3. the same ``session_id`` continues a previous conversation,
4. different ``session_id`` values keep their own history,
5. the Extra engine is built once and reused for every request.

## Files

```
examples/mcp-server/
├── agents.yaml              # one-agent system
├── prompts/echo_agent/system.md
├── plugins/                 # generated stub + implementation
│   ├── plugins.toml
│   └── tools/echo.py
└── client.py                # tiny MCP client that drives the server
```

## Run it

The example uses Ollama through the OpenAI-compatible API by default, just
like `examples/starter`. Make sure Ollama is running and the model is pulled:

```bash
ollama pull qwen2.5:14b
```

Then start the server in one terminal:

```bash
agentctl mcp serve --config examples/mcp-server/agents.yaml
```

And run the bundled client in another:

```bash
python examples/mcp-server/client.py
```

If you want to use a different model, change `defaults.model.name` in
`agents.yaml` and set the corresponding provider key in your environment
(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.).

The server speaks MCP over stdio, so any MCP-aware client can point at it.
For example, to wire it into Claude Desktop, add an entry like:

```json
{
  "mcpServers": {
    "extra": {
      "command": "agentctl",
      "args": ["mcp", "serve", "--config", "/abs/path/to/agents.yaml"]
    }
  }
}
```

## What the tool looks like

The server exposes a single tool, ``extra_chat``:

```json
{
  "message": "Search the internal documentation",
  "session_id": "optional-session-id",
  "user_id": "optional-user-id"
}
```

Response:

```json
{
  "session_id": "abc123",
  "answer": "The relevant documentation is...",
  "visited": ["root", "knowledge_agent"],
  "used_tools": [{"name": "search_internal_documents", "provider": "local"}]
}
```

## How it works

The MCP layer in ``agentctl/mcp_serve.py`` does only five things:

1. validate the YAML spec,
2. open the existing application repositories (one process-lifetime DB),
3. build the existing ``LangGraphEngine`` once,
4. on each tool call, create or load a session and call the existing
   ``ConversationService.send``,
5. return the ``RunResult`` as an MCP ``TextContent`` block.

Routing, tool execution, approvals, hooks, and access control are not
re-implemented — they continue to live in the Extra runtime.