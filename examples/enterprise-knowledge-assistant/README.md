# Enterprise Knowledge Assistant

This flagship example demonstrates a declarative multi-agent system with local
tools, remote MCP servers, runtime hooks, and a self-contained local MCP server.
The local server provides deterministic data for proving that “Approve for this
session” is scoped to one canonical MCP tool identity rather than every tool on
the server. The interactive runner uses the real LLM provider and model declared
in `local_mcp_agents.yaml`.

## Local MCP tools

The `local_knowledge_mcp` server exposes three deterministic mock tools:

| Tool | Behavior |
| --- | --- |
| `search_internal_documents` | Searches mock company documents |
| `get_employee_information` | Returns mock employee and team information |
| `publish_internal_note` | Simulates publishing without persisting data |

All three tools use the normal Extra MCP Streamable HTTP integration. They are
not registered as local Python agent tools. The MCP itself requires no external
API, credential, database, or internet connection. The configured LLM still
requires its normal provider credentials. The focused runner configures its
agent with `auto: false`, so every one of these MCP tools requires approval
unless its exact canonical identity is already approved for the current
session.

## Installation

From the project root:

```bash
cd /Users/spwrwn/projects/extra
make install
```

The installed project dependencies include the MCP Python SDK and
`langchain-mcp-adapters`.

Copy the example environment file and configure the provider already selected
in `local_mcp_agents.yaml`:

```bash
cd /Users/spwrwn/projects/extra
cp examples/enterprise-knowledge-assistant/.env.example \
  examples/enterprise-knowledge-assistant/.env
```

The checked-in configuration currently uses the same
`anthropic/claude-haiku-4-5` model as the flagship example, so set
`ANTHROPIC_API_KEY` in that private `.env`. If the YAML is changed to another
supported provider, configure that provider through the repository's existing
standard environment mechanism; the runner does not hardcode a provider or API
key.

## Start the local MCP

In terminal 1:

```bash
cd /Users/spwrwn/projects/extra/examples/enterprise-knowledge-assistant
python3 -m mcps.local_knowledge_mcp.server
```

The Streamable HTTP endpoint is `http://127.0.0.1:8765/mcp`.

## Run the interactive approval example

In terminal 2:

```bash
cd /Users/spwrwn/projects/extra
python3 examples/enterprise-knowledge-assistant/run_local_mcp_approval.py
```

This runner uses the production `LangGraphEngine`, the real in-memory approval
repository, the existing in-memory conversation repository, the real configured
LLM, and the real local MCP transport. The conversation repository and approval
repository are separate, but both are scoped by the same session ID. The LLM
receives structured prior user/assistant messages plus the latest
natural-language message, discovers the bound tool schemas, selects a tool,
generates arguments, receives the result after approval, and writes the final
response.

To use a different environment file:

```bash
python3 examples/enterprise-knowledge-assistant/run_local_mcp_approval.py \
  --env /absolute/path/to/provider.env
```

If the configured provider cannot initialize, startup fails with the provider
and model names plus a pointer to `.env.example`; secret values and underlying
provider error details are not printed.

## Exact manual approval sequence

Enter these natural-language prompts in the interactive runner. They avoid MCP
implementation names while clearly expressing the intended business action.

1. Enter:

   ```text
   Find internal documents about the session approval security policy and summarize them.
   ```

   Choose `2` — Approve for this session.

2. Ask another document question:

   ```text
   Look through our internal documents for publishing guidelines and summarize what you find.
   ```

   The LLM should select the same document-search tool. It must execute with
   `requested=false source=session_cache` and no prompt.

3. Enter:

   ```text
   Who is employee E-100, what is their role, and which team are they on?
   ```

   It is a different canonical tool identity on the same MCP server, so it must
   prompt. Choose `2`.

4. Ask both approved categories again, one message at a time:

   ```text
   Search our internal documents for information about enterprise knowledge search.
   Tell me about employee E-200 and their team.
   ```

   Neither call should prompt.

5. Enter:

   ```text
   Publish an internal note titled "Approval demonstration" saying that session approvals were tested.
   ```

   It must request its own approval. Choose `3` to deny it and verify
   `executed=false status=denied`. The final LLM response should acknowledge
   that publishing was denied rather than claiming the note was created.

6. Enter:

   ```text
   /new-session
   Find internal documents about the session approval security policy and summarize them.
   ```

   The new session must show a cache miss and request approval again.

Use `/session` to show the current session, `/tools` to list commands, and
`/exit` to stop the runner.

## Exact conversational follow-up sequence

In one session, enter:

```text
Find internal documents about the session approval security policy and summarize them.
```

Choose `2` to approve the selected search tool for the session. After the
assistant returns the results and numbered follow-up choices, enter:

```text
1
```

The terminal must show `messages_before_run=2` and `message_count=3` before the
second run. The model should interpret the number using the previous assistant
response, perform the selected follow-up search, and execute without another
approval prompt. The same behavior can be tested with:

```text
Search more broadly.
What about authentication policies?
Summarize the second result.
Use the same search, but only for engineering documents.
```

Then enter:

```text
/new-session
1
```

The new session must show `messages_before_run=0`. The model must not know about
the previous session's numbered choices, and no previous approval may be reused.

## Expected safe terminal events

The runner prints events such as:

```text
[SESSION STARTED] session_id=local-session-...
[MODEL CONFIGURED] provider=... model=...
[MCP DISCOVERED] server=local_knowledge_mcp tools=...
[USER MESSAGE] session_id=... text=chars=... content=omitted
[SESSION HISTORY] session_id=... messages_before_run=...
[SESSION MESSAGE APPENDED] session_id=... role=user
[MODEL CONTEXT] session_id=... message_count=...
[MODEL INVOCATION] session_id=... provider=... model=... phase=started
[MODEL TOOL SELECTION] session_id=... server=local_knowledge_mcp tool=... identity=mcp:local_knowledge_mcp:...
[APPROVAL CACHE] session_id=... tool=mcp:local_knowledge_mcp:... source=session_cache hit=false
[APPROVAL] session_id=... server=local_knowledge_mcp tool=... requested=true source=user
[APPROVAL STORED] session_id=... tool=mcp:local_knowledge_mcp:... decision=allow_for_session
[TOOL EXECUTION] session_id=... server=local_knowledge_mcp tool=... executed=true status=succeeded
[TOOL RESULT] session_id=... tool=mcp:local_knowledge_mcp:... returned_to_llm=true kind=mcp_result
[SESSION MESSAGE APPENDED] session_id=... role=assistant
[FINAL ASSISTANT RESPONSE] session_id=...
```

The user-message event records only character count; message content is omitted.
Generated tool argument values, tool-result payloads, tokens, API keys,
authentication headers, and secrets are never logged.

## Automated and live validation

Automated tests remain offline and deterministic. They start the local MCP,
verify discovery through `LangGraphEngine`, exercise the real approval
coordinator and session repository, and validate per-tool/session isolation and
denial. A deterministic context-aware fake model also asserts the exact
structured user/assistant history for numeric and natural-language follow-ups,
calls the real local MCP through the approval gate, receives its `ToolMessage`,
and proves that the approved search tool is reused without another prompt. No
test calls an external model provider.

The real flow is intentionally a documented manual integration test because
live model selection is networked, credentialed, billable, and not deterministic:

```text
real configured LLM → tool selection → approval → session cache
→ local MCP → tool result returned to LLM → final response
```

## Flagship configuration

The main `agents.yaml` registers `local_knowledge_mcp` and makes it available to
`enterprise_docs_agent` alongside Context7. The runner uses
`local_mcp_agents.yaml`, a focused single-agent projection that prevents remote
MCP availability from affecting this approval test while retaining the
flagship example's configured provider and model.
