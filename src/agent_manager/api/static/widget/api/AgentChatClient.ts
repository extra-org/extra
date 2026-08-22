import type { TokenSource } from "../auth/tokenSource";
import type {
  ApprovalDecision,
  ChatMessage,
  PaginatedThreads,
  TokenBudget,
  SendMessageResponse,
  StreamEvent,
  ThreadSummary,
} from "../types";

export class AgentChatHttpError extends Error {
  constructor(
    readonly status: number,
    readonly errorType?: string,
    message?: string,
  ) {
    super(message || `HTTP ${status}`);
    this.name = "AgentChatHttpError";
  }
}

async function handleHttpError(response: Response): Promise<never> {
  let errorType: string | undefined;
  let message: string | undefined;
  try {
    const body = await response.json();
    if (typeof body.detail === "object" && body.detail !== null) {
      errorType = body.detail.error_type;
      message = body.detail.message;
    } else if (typeof body.detail === "string") {
      message = body.detail;
    }
  } catch {}
  throw new AgentChatHttpError(response.status, errorType, message);
}

export class AgentChatClient {
  constructor(
    private readonly endpoint: string,
    private readonly tokens: TokenSource,
  ) {}

  /** A 401 usually means the token expired: renew once and retry. The rejected
   *  attempt changed nothing, so replaying is safe. */
  private async request(path: string, init?: RequestInit): Promise<Response> {
    let response = await this.send(path, init, await this.tokens.current());
    if (response.status === 401) {
      response = await this.send(path, init, await this.tokens.renew());
    }
    if (!response.ok) {
      await handleHttpError(response);
    }
    return response;
  }

  private send(path: string, init: RequestInit | undefined, token: string | null) {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    // `include` lets a same-origin deployment authenticate by the host's own
    // cookie, which the widget can never read.
    return fetch(`${this.endpoint}${path}`, { ...init, headers, credentials: "include" });
  }

  async createConversation(): Promise<string> {
    const response = await this.request("/conversations", { method: "POST", body: "{}" });
    const data = await response.json();
    return String(data.conversation_id);
  }

  async listConversations(limit = 20, cursor?: string | null): Promise<PaginatedThreads> {
    const params = new URLSearchParams({ limit: String(limit) });
    if (cursor) params.set("cursor", cursor);
    const response = await this.request(`/conversations?${params.toString()}`);

    const data = await response.json();
    const rawItems = Array.isArray(data.items) ? data.items : [];
    const items: ThreadSummary[] = rawItems.map((thread: any) => ({
      conversation_id: String(thread.conversation_id),
      title: thread.title ?? null,
      last_message_at: thread.last_message_at ?? null,
    }));
    return {
      items,
      next_cursor: data.next_cursor ? String(data.next_cursor) : null,
    };
  }

  async getMessages(conversationId: string): Promise<ChatMessage[]> {
    const response = await this.request(`/conversations/${conversationId}/messages`);

    return (await response.json()) as ChatMessage[];
  }

  async sendMessage(
    conversationId: string,
    message: string,
    signal?: AbortSignal,
    editMessageId?: string,
  ): Promise<SendMessageResponse> {
    const response = await this.request(`/conversations/${conversationId}/messages`, {
      method: "POST",
      body: JSON.stringify({ message, edit_message_id: editMessageId }),
      signal,
    });

    return parseRunResponse(await response.json());
  }

  async decideApproval(
    conversationId: string,
    runId: string,
    approvalId: string,
    decision: ApprovalDecision,
  ): Promise<SendMessageResponse> {
    const response = await this.request(
      `/conversations/${conversationId}/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}/decision`,
      {
        method: "POST",
        body: JSON.stringify({ decision }),
      },
    );
    return parseRunResponse(await response.json());
  }

  async *streamApproval(
    conversationId: string,
    runId: string,
    approvalId: string,
    decision: ApprovalDecision,
    signal?: AbortSignal,
  ): AsyncGenerator<StreamEvent> {
    const response = await this.request(
      `/conversations/${conversationId}/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}/decision/stream`,
      {
        method: "POST",
        body: JSON.stringify({ decision }),
        signal,
      },
    );
    yield* readSseResponse(response);
  }

  async cancelApproval(
    conversationId: string,
    runId: string,
    approvalId: string,
  ): Promise<void> {
    await this.request(
      `/conversations/${conversationId}/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}/cancel`,
      { method: "POST", body: "{}" },
    );
  }

  async getUsage(conversationId: string): Promise<TokenBudget> {
    const response = await this.request(`/conversations/${conversationId}/usage`);
    const data = await response.json();
    return {
      used_tokens: Number(data.used_tokens) || 0,
      max_tokens: data.max_tokens == null ? null : Number(data.max_tokens),
      percent: Number(data.percent) || 0,
      severity: data.severity ?? "normal",
    };
  }

  async *streamMessage(
    conversationId: string,
    message: string,
    signal?: AbortSignal,
    editMessageId?: string,
  ): AsyncGenerator<StreamEvent> {
    const response = await this.request(`/conversations/${conversationId}/messages/stream`, {
      method: "POST",
      body: JSON.stringify({ message, edit_message_id: editMessageId }),
      signal,
    });

    yield* readSseResponse(response);
  }
}

async function* readSseResponse(response: Response): AsyncGenerator<StreamEvent> {
  if (!response.body) {
    throw new Error("Streaming response has no body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const event = parseSseFrame(frame);
        if (event) yield event;
      }
    }
    buffer += decoder.decode();
    const event = parseSseFrame(buffer);
    if (event) yield event;
  } finally {
    try {
      await reader.cancel();
    } catch {
      // An AbortSignal may already have cancelled the response body.
    }
    reader.releaseLock();
  }
}

function parseRunResponse(data: Record<string, unknown>): SendMessageResponse {
  return {
    answer: String(data.answer || ""),
    visited: Array.isArray(data.visited) ? (data.visited as string[]) : undefined,
    used_tools: Array.isArray(data.used_tools) ? data.used_tools : undefined,
    status: data.status === "pending_approval" ? "pending_approval" : "completed",
    run_id: data.run_id == null ? null : String(data.run_id),
    pending_approval:
      typeof data.pending_approval === "object" && data.pending_approval !== null
        ? (data.pending_approval as SendMessageResponse["pending_approval"])
        : null,
  };
}

function parseSseFrame(frame: string): StreamEvent | null {
  const dataLines = frame
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice("data:".length).trimStart());
  if (!dataLines.length) return null;
  const data = dataLines.join("\n");
  if (!data || data === "[DONE]") return null;
  return JSON.parse(data) as StreamEvent;
}
