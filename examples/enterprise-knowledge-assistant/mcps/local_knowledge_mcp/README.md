# Local Knowledge MCP

This Streamable HTTP MCP server supplies deterministic mock enterprise data for
the session-approval example. It has no external service, network, database, or
credential dependency.

Available tools:

- `search_internal_documents(query)` searches mock internal documents.
- `get_employee_information(employee_id)` returns mock employee and team data.
- `publish_internal_note(title, content)` simulates publishing without storing
  anything.

From `/Users/spwrwn/projects/extra`, start it by setting the example directory
as the module root:

```bash
cd /Users/spwrwn/projects/extra/examples/enterprise-knowledge-assistant
python3 -m mcps.local_knowledge_mcp.server
```

The server listens on `http://127.0.0.1:8765/mcp`. It logs only its server and
tool names when execution occurs; arguments and values are never logged.
