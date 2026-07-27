import type { MessageEntry, StreamEvent, ToolRecord } from "../types";

const TOOL_STATUS: Record<string, string> = {
  tool_started: "started",
  tool_succeeded: "succeeded",
  tool_failed: "failed",
};

export function reduceStreamEvent(entry: MessageEntry, event: StreamEvent): MessageEntry {
  switch (event.type) {
    case "answer_delta":
      return { ...entry, text: entry.text + (event.content ?? "") };
    case "route":
      return { ...entry, route: event.route ?? entry.route };
    case "tool_started":
    case "tool_succeeded":
    case "tool_failed":
      return { ...entry, tools: upsertTool(entry.tools ?? [], toToolRecord(event)) };
    case "final":
      return {
        ...entry,
        text: event.content ?? entry.text,
        route: event.route ?? entry.route,
        tools: event.used_tools ?? entry.tools,
      };
    case "error":
      throw new Error(event.error || "stream failed");
    default:
      return entry;
  }
}

function toToolRecord(event: StreamEvent): ToolRecord {
  return {
    name: event.tool_name ?? "tool",
    provider: event.provider ?? "runtime",
    status: TOOL_STATUS[event.type] ?? "started",
    server_id: event.server_id,
    error: event.error,
  };
}

function upsertTool(tools: ToolRecord[], next: ToolRecord): ToolRecord[] {
  const index = tools.findIndex((tool) => tool.name === next.name && tool.provider === next.provider);
  if (index === -1) return [...tools, next];
  return tools.map((tool, position) => (position === index ? { ...tool, ...next } : tool));
}
