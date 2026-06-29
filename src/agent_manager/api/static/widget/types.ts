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
