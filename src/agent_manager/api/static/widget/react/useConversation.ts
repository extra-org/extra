import { useCallback, useMemo } from "react";

import { AgentChatHttpError, type AgentChatClient } from "../api/AgentChatClient";
import {
  getStoredConversationId,
  removeStoredConversationId,
  setStoredConversationId,
} from "../storage/conversationStorage";
import type { ChatMessage, ContextUsage, SendMessageResponse, StreamEvent } from "../types";

export interface Conversation {
  send(text: string): Promise<SendMessageResponse>;
  stream(text: string): AsyncGenerator<StreamEvent>;
  loadHistory(): Promise<ChatMessage[]>;
  loadUsage(): Promise<ContextUsage | null>;
}

const isMissingConversation = (error: unknown): boolean =>
  error instanceof AgentChatHttpError && error.status === 404;

export function useConversation(client: AgentChatClient, endpoint: string): Conversation {
  const startConversation = useCallback(async () => {
    const created = await client.createConversation();
    setStoredConversationId(endpoint, created);
    return created;
  }, [client, endpoint]);

  const ensureId = useCallback(
    async () => getStoredConversationId(endpoint) ?? startConversation(),
    [endpoint, startConversation],
  );

  const restartId = useCallback(async () => {
    removeStoredConversationId(endpoint);
    return startConversation();
  }, [endpoint, startConversation]);

  const send = useCallback(
    async (text: string) => {
      try {
        return await client.sendMessage(await ensureId(), text);
      } catch (error) {
        if (!isMissingConversation(error)) throw error;
        return client.sendMessage(await restartId(), text);
      }
    },
    [client, ensureId, restartId],
  );

  const stream = useCallback(
    async function* (text: string): AsyncGenerator<StreamEvent> {
      try {
        yield* client.streamMessage(await ensureId(), text);
      } catch (error) {
        if (!isMissingConversation(error)) throw error;
        yield* client.streamMessage(await restartId(), text);
      }
    },
    [client, ensureId, restartId],
  );

  const loadHistory = useCallback(async () => {
    const stored = getStoredConversationId(endpoint);
    if (!stored) return [];
    try {
      return await client.getMessages(stored);
    } catch (error) {
      if (isMissingConversation(error)) removeStoredConversationId(endpoint);
      return [];
    }
  }, [client, endpoint]);

  const loadUsage = useCallback(async () => {
    const stored = getStoredConversationId(endpoint);
    if (!stored) return null;
    try {
      return await client.getUsage(stored);
    } catch {
      return null;
    }
  }, [client, endpoint]);

  return useMemo(
    () => ({ send, stream, loadHistory, loadUsage }),
    [send, stream, loadHistory, loadUsage],
  );
}
