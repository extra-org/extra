export type AgentChatPosition = "bottom-right" | "bottom-left";
export type AgentChatMode = "floating" | "inline";
export type ChatRole = "user" | "assistant" | "system" | "tool" | "orchestrator" | "agent";

export interface AgentChatConfig {
  endpoint: string;
  title: string;
  color: string;
  greeting: string;
  position: AgentChatPosition;
  avatar: string;
  mode: AgentChatMode;
}

export interface AgentChatConfigInput {
  endpoint?: string;
  title?: string;
  color?: string;
  greeting?: string;
  position?: string;
  avatar?: string;
  mode?: string;
}

export interface ChatMessage {
  role: ChatRole;
  content: string;
  created_at?: string;
}

export type ContextSeverity = "normal" | "warning" | "critical";

export interface ContextUsage {
  used_tokens: number;
  max_tokens: number | null;
  percent: number;
  severity: ContextSeverity;
}

export interface MessageEntry {
  id: string;
  role: "user" | "ai";
  text: string;
  typing?: boolean;
  error?: boolean;
  route?: string[];
  tools?: ToolRecord[];
}

export interface ToolRecord {
  name: string;
  provider: string;
  status: string;
  agent_id?: string | null;
  server_id?: string | null;
  error?: string | null;
}

export interface SendMessageResponse {
  answer: string;
  /** Routing path through the agent graph, e.g. ["router", "router/sub_agent"]. */
  visited?: string[];
  /** Tools observed during the run. */
  used_tools?: ToolRecord[];
}

export interface StreamEvent {
  type:
    | "route"
    | "answer_delta"
    | "tool_started"
    | "tool_succeeded"
    | "tool_failed"
    | "final"
    | "error";
  content?: string;
  route?: string[];
  tool_name?: string;
  provider?: string;
  server_id?: string;
  status?: string;
  error?: string;
  system_name?: string;
  used_tools?: ToolRecord[];
}

/** Detail of the `agent-chat:answer` event a host page can listen for. */
export interface AgentChatAnswerDetail {
  visited: string[];
  used_tools: ToolRecord[];
}

declare global {
  interface Window {
    agentChatConfig?: AgentChatConfigInput;
  }
}
