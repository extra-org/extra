import type {
  ChatMessage,
  ContextUsage,
  SendMessageResponse,
  StreamEvent,
  ThreadSummary,
} from "../types";

export class AgentChatHttpError extends Error {
  constructor(readonly status: number) {
    super(`HTTP ${status}`);
    this.name = "AgentChatHttpError";
  }
}

export class AgentChatClient {
  constructor(private readonly endpoint: string) {}

  async createConversation(userId: string): Promise<string> {
    const response = await fetch(`${this.endpoint}/conversations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId }),
    });
    if (!response.ok) {
      throw new AgentChatHttpError(response.status);
    }
    const data = await response.json();
    return String(data.conversation_id);
  }

  async listConversations(userId: string): Promise<ThreadSummary[]> {
    const url = `${this.endpoint}/conversations?user_id=${encodeURIComponent(userId)}`;
    const response = await fetch(url);
    if (!response.ok) {
      throw new AgentChatHttpError(response.status);
    }
    const data = await response.json();
    if (!Array.isArray(data)) return [];
    return data.map((thread) => ({
      conversation_id: String(thread.conversation_id),
      title: thread.title ?? null,
      last_message_at: thread.last_message_at ?? null,
    }));
  }

  async getMessages(conversationId: string): Promise<ChatMessage[]> {
    const response = await fetch(`${this.endpoint}/conversations/${conversationId}/messages`);
    if (!response.ok) {
      throw new AgentChatHttpError(response.status);
    }
    return (await response.json()) as ChatMessage[];
  }

  async sendMessage(conversationId: string, message: string): Promise<SendMessageResponse> {
    const response = await fetch(`${this.endpoint}/conversations/${conversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    if (!response.ok) {
      throw new AgentChatHttpError(response.status);
    }
    const data = await response.json();
    return {
      answer: String(data.answer || ""),
      visited: Array.isArray(data.visited) ? (data.visited as string[]) : undefined,
      used_tools: Array.isArray(data.used_tools) ? data.used_tools : undefined,
    };
  }

  async getUsage(conversationId: string): Promise<ContextUsage> {
    const response = await fetch(`${this.endpoint}/conversations/${conversationId}/usage`);
    if (!response.ok) {
      throw new AgentChatHttpError(response.status);
    }
    const data = await response.json();
    return {
      used_tokens: Number(data.used_tokens) || 0,
      max_tokens: data.max_tokens == null ? null : Number(data.max_tokens),
      percent: Number(data.percent) || 0,
      severity: data.severity ?? "normal",
    };
  }

  async *streamMessage(conversationId: string, message: string): AsyncGenerator<StreamEvent> {
    const response = await fetch(`${this.endpoint}/conversations/${conversationId}/messages/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    if (!response.ok) {
      throw new AgentChatHttpError(response.status);
    }
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
        const frames = buffer.split(/\n\n/);
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
      reader.releaseLock();
    }
  }
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
