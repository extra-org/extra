import type { ChatMessage, SendMessageResponse } from "../types";

export class AgentChatClient {
  constructor(private readonly endpoint: string) {}

  async createConversation(): Promise<string> {
    const response = await fetch(`${this.endpoint}/conversations`, { method: "POST" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    return String(data.conversation_id);
  }

  async getMessages(conversationId: string): Promise<ChatMessage[]> {
    const response = await fetch(`${this.endpoint}/conversations/${conversationId}/messages`);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
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
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    return {
      answer: String(data.answer || ""),
      visited: Array.isArray(data.visited) ? (data.visited as string[]) : undefined,
      used_tools: Array.isArray(data.used_tools) ? data.used_tools : undefined,
    };
  }
}
