# MCP & Tools

This document defines how executor agents use Python plugin tools and MCP
servers. Implementation is task `0007`.

---

## MCP Servers

MCP servers are declared once and referenced by agents:

```yaml
mcps:
  flights_mcp:
    url: "https://company.com/mcp/flights/sse"

agents:
  domestic_flights_agent:
    description: "Search and book flights within the country."
    mcps: [flights_mcp]
```

MCP servers may be implemented in any language. The engine connects to them from
the long-lived runtime and exposes their discovered tools to configured agents.

---

## Python Plugin Tools

Tools are Python plugin methods exposed to the LLM at runtime:

```yaml
tools:
  book_flight:
    class: FlightTools
    method: book_flight

agents:
  domestic_flights_agent:
    description: "Search and book flights within the country."
    tools: [book_flight]
```

Plugin shape:

```python
class FlightTools:
    def __init__(self):
        ...

    def book_flight(self, ctx, **kwargs):
        ...
```

The engine loads plugin class instances once so customers can keep shared state
such as database pools, REST clients, auth clients, or caches.

---

## Resolver vs. Tool Boundary

| | Resolver | Tool |
| --- | --- | --- |
| Runs | Before the node runs | During LLM execution |
| Chosen by | Engine | LLM |
| Exposed to LLM | No | Yes |
| Token cost | None | Yes |
| Purpose | Fill prompt variables | Perform actions |

Use a resolver for deterministic context such as `current_date`, `user_name`, or
`subscription`. Use a tool for model-selected actions such as `book_flight` or
`add_to_cart`.

---

## Safety

The current schema does not yet define per-tool permissions or input policies.
For the MVP:

- validate that every agent tool id exists in top-level `tools`;
- validate that every agent MCP id exists in top-level `mcps`;
- load plugin references explicitly by class and method;
- pass request context through `ctx`;
- redact secrets from traces;
- keep prompt wording out of the enforcement path.

Future per-tool access control should be added deliberately to the schema and
docs before implementation.
