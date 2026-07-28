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
  user: string;
}

export interface AgentChatConfigInput {
  endpoint?: string;
  title?: string;
  color?: string;
  greeting?: string;
  position?: string;
  avatar?: string;
  mode?: string;
  user?: string;
}

export interface ThreadSummary {
  conversation_id: string;
  title: string | null;
  last_message_at: string | null;
}

export interface ChatMessage {
  role: ChatRole;
  content: string;
  created_at?: string;
  message_id?: string;
  feedback?: "thumbs_up" | "thumbs_down" | null;
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
  message_id?: string;
  role: "user" | "ai";
  text: string;
  typing?: boolean;
  error?: boolean;
  route?: string[];
  tools?: ToolRecord[];
  feedback?: "thumbs_up" | "thumbs_down" | null;
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
  message_id?: string;
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
  message_id?: string;
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
