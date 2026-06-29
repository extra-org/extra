# Embeddable Chat Widget

The browser widget is a framework-agnostic custom element served as an ES
module. Host apps still embed a plain `<agent-chat>` element, while the widget
mounts a React chat surface internally inside its Shadow DOM. That keeps Angular
/ Vue / server-rendered pages isolated from the widget implementation while
letting the chat UI use shadcn AI Elements-style primitives:

- `Conversation` powered by `use-stick-to-bottom`.
- `Message` / `MessageContent` / `MessageResponse` powered by `streamdown`.
- `PromptInput` / `PromptInputTextarea` / `PromptInputSubmit`.
- `Tool` / `ToolHeader` / `ToolContent` / `ToolOutput` for agent/tool activity.

```html
<script type="module" src="/widget.js"></script>
<agent-chat title="Support" color="#2563eb"></agent-chat>
```

## Floating Mode

```html
<script type="module" src="https://your-backend.example/widget.js"></script>
<agent-chat
  title="Support"
  color="#2563eb"
  greeting="Hi! How can I help?"
  mode="floating"
  position="bottom-right">
</agent-chat>
```

## Inline Mode

```html
<script type="module" src="https://your-backend.example/widget.js"></script>
<agent-chat
  title="Inline Assistant"
  color="#7c3aed"
  mode="inline">
</agent-chat>
```

## Script-Only Auto-Mount

When no `<agent-chat>` exists on the page, `window.agentChatConfig` can create
one automatically:

```html
<script>
  window.agentChatConfig = {
    title: "Auto-mounted Assistant",
    color: "#16a34a",
    greeting: "Hi from auto-mount"
  };
</script>
<script type="module" src="https://your-backend.example/widget.js"></script>
```

## Host Frameworks

The host framework does not need to install React, Streamdown, or the AI UI
dependencies. They are bundled inside `widget.js`; the public integration
contract is still the custom element, attributes, and DOM events.

React can render the custom element directly:

```tsx
export function HelpChat() {
  return <agent-chat title="Support" color="#2563eb" />;
}
```

Angular apps should allow custom elements in the owning module:

```ts
import { CUSTOM_ELEMENTS_SCHEMA, NgModule } from "@angular/core";

@NgModule({
  schemas: [CUSTOM_ELEMENTS_SCHEMA],
})
export class AppModule {}
```

Then use the element in a template:

```html
<agent-chat title="Support" color="#2563eb"></agent-chat>
```

## Local Demo Pages

Run the API/static server, then open:

- `/widget-demo.html` for floating mode.
- `/widget-demo-inline.html` for inline mode.
- `/widget-demo-automount.html` for script-only auto-mount.
- `/widget-demo-attribute-override.html` for authored element attributes with a global config present.

The Playwright smoke tests mock the conversation endpoints for deterministic
browser coverage. For a manual test against the real API, run `agent-manager`
with a valid agent config and open the same demo pages.

## Real Agent / Sub-Agent Flow Demo

`/widget-agent-flow-demo.html` proves the full product path with **no mocks**:
the widget talks to the real `/conversations` API, which runs the agent engine,
which routes through a root orchestrator to a sub-agent and back.

Run the deterministic demo config (root `concierge_router` → `tags_agent` /
`hours_agent` sub-agents; Anthropic only, no MCP, no protected nodes):

```bash
agent-manager --config examples/widget_sub_agent_demo.yml --env .env --port 8100
```

Open `http://127.0.0.1:8100/widget-agent-flow-demo.html`, open the widget, and
try:

- `show me the available document tags` → routes to `tags_agent`
- `what are your support hours?` → routes to `hours_agent`

**Proof the sub-agent ran.** The `/conversations/{id}/messages` response
includes a safe `visited` routing path (e.g.
`["concierge_router", "concierge_router/tags_agent"]`). The widget emits this as
an `agent-chat:answer` DOM event (and a `console.debug` line); the demo page
renders it in a live "Routing evidence" panel. The same path appears in the
server logs (`run ended … visited=…`). Only routing/tool metadata is exposed —
never reasoning or hidden content.

Host pages can consume the same signal:

```js
document.addEventListener("agent-chat:answer", (e) => {
  console.log(e.detail.visited, e.detail.used_tools);
});
```
