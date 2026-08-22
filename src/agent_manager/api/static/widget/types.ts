export type AgentChatPosition = "bottom-right" | "bottom-left";
export type AgentChatMode = "floating" | "inline";
export type ChatRole = "user" | "assistant" | "system" | "tool" | "orchestrator" | "agent";
export type ApprovalDecision = "allow_once" | "deny" | "allow_for_session";

export interface AgentChatConfig {
  endpoint: string;
  title: string;
  color: string;
  greeting: string;
  position: AgentChatPosition;
  avatar: string;
  mode: AgentChatMode;
  tokenUrl: string;
  requireIdentity: boolean;
}

export interface AgentChatConfigInput {
  endpoint?: string;
  title?: string;
  color?: string;
  greeting?: string;
  position?: string;
  avatar?: string;
  mode?: string;
  tokenUrl?: string;
  requireIdentity?: boolean;
}

export interface ThreadSummary {
  conversation_id: string;
  title: string | null;
  last_message_at: string | null;
}

export interface PaginatedThreads {
  items: ThreadSummary[];
  next_cursor: string | null;
}

export interface ChatMessage {
  message_id: string;
  run_id?: string | null;
  role: ChatRole;
  content: string;
  status: string;
  created_at?: string;
}

export type BudgetSeverity = "normal" | "warning" | "critical";

/** Cumulative tokens spent by a conversation against its configured budget. */
export interface TokenBudget {
  used_tokens: number;
  max_tokens: number | null;
  percent: number;
  severity: BudgetSeverity;
}

export interface MessageEntry {
  id: string;
  messageId?: string;
  runId?: string;
  role: "user" | "ai";
  text: string;
  status?: "cancelled";
  typing?: boolean;
  error?: boolean;
  route?: string[];
  tools?: ToolRecord[];
  approval?: PendingApproval;
  approvalSubmitting?: boolean;
  approvalCancelling?: boolean;
  approvalError?: string;
}

export interface PendingApproval {
  run_id: string;
  approval_id: string;
  agent_id: string;
  tool_name: string;
  description: string;
  provider?: string;
  server_id?: string | null;
  arguments?: Record<string, unknown>;
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
  status?: "completed" | "pending_approval";
  run_id?: string | null;
  pending_approval?: PendingApproval | null;
}

export interface StreamEvent {
  type:
    | "route"
    | "turn_started"
    | "resume_started"
    | "answer_delta"
    | "tool_started"
    | "tool_succeeded"
    | "tool_failed"
    | "final"
    | "pending_approval"
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
  run_id?: string;
  message_id?: string;
  approval_id?: string;
  agent_id?: string;
  description?: string;
  arguments?: Record<string, unknown>;
}

/** Detail of the `agent-chat:identity-error` event, raised when a configured
 *  host identity could not be obtained. */
export interface AgentChatIdentityErrorDetail {
  reason: "unauthorized" | "unreachable" | "malformed";
  status?: number;
  url: string;
  /** Whether an anonymous fallback is permitted at all — capability, not
   *  outcome. The fallback can itself fail, and a host session cookie may end
   *  up authenticating the next request instead. */
  anonymousFallbackEnabled: boolean;
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
