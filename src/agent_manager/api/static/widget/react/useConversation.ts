import { useCallback, useMemo } from "react";

import { AgentChatHttpError, type AgentChatClient } from "../api/AgentChatClient";
import {
  getStoredConversationId,
  removeStoredConversationId,
  setStoredConversationId,
} from "../storage/conversationStorage";
import type {
  ChatMessage,
  TokenBudget,
  SendMessageResponse,
  StreamEvent,
  ThreadSummary,
} from "../types";

export interface Conversation {
  peekId(): string | null;
  ensureId(): Promise<string>;
  send(text: string): Promise<SendMessageResponse>;
  stream(text: string): AsyncGenerator<StreamEvent>;
  loadHistory(): Promise<ChatMessage[]>;
  loadUsage(): Promise<TokenBudget | null>;
  listThreads(): Promise<ThreadSummary[]>;
  switchTo(conversationId: string): void;
  startNew(): void;
}

/** A stored conversation the server will not serve us: gone (404), or owned by
 *  someone else (403) — which is what a host app switching users looks like,
 *  since the stored id outlives the identity that created it. Both are
 *  recovered the same way, by starting a fresh conversation. */
const isUnusableConversation = (error: unknown): boolean =>
  error instanceof AgentChatHttpError && (error.status === 404 || error.status === 403);

export function useConversation(
  client: AgentChatClient,
  endpoint: string,
  userId: string,
): Conversation {
  const startConversation = useCallback(async () => {
    const created = await client.createConversation();
    setStoredConversationId(endpoint, userId, created);
    return created;
  }, [client, endpoint, userId]);

  const peekId = useCallback(
    () => getStoredConversationId(endpoint, userId),
    [endpoint, userId],
  );

  const ensureId = useCallback(
    async () => getStoredConversationId(endpoint, userId) ?? startConversation(),
    [endpoint, userId, startConversation],
  );

  const restartId = useCallback(async () => {
    removeStoredConversationId(endpoint, userId);
    return startConversation();
  }, [endpoint, userId, startConversation]);

  const send = useCallback(
    async (text: string) => {
      try {
        return await client.sendMessage(await ensureId(), text);
      } catch (error) {
        if (!isUnusableConversation(error)) throw error;
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
        if (!isUnusableConversation(error)) throw error;
        yield* client.streamMessage(await restartId(), text);
      }
    },
    [client, ensureId, restartId],
  );

  const loadHistory = useCallback(async () => {
    const stored = getStoredConversationId(endpoint, userId);
    if (!stored) return [];
    try {
      return await client.getMessages(stored);
    } catch (error) {
      if (isUnusableConversation(error)) removeStoredConversationId(endpoint, userId);
      return [];
    }
  }, [client, endpoint, userId]);

  const loadUsage = useCallback(async () => {
    const stored = getStoredConversationId(endpoint, userId);
    if (!stored) return null;
    try {
      return await client.getUsage(stored);
    } catch {
      return null;
    }
  }, [client, endpoint, userId]);

  const listThreads = useCallback(() => client.listConversations().catch(() => []), [client]);

  const switchTo = useCallback(
    (conversationId: string) => setStoredConversationId(endpoint, userId, conversationId),
    [endpoint, userId],
  );

  const startNew = useCallback(
    () => removeStoredConversationId(endpoint, userId),
    [endpoint, userId],
  );

  return useMemo(
    () => ({
      peekId,
      ensureId,
      send,
      stream,
      loadHistory,
      loadUsage,
      listThreads,
      switchTo,
      startNew,
    }),
    [peekId, ensureId, send, stream, loadHistory, loadUsage, listThreads, switchTo, startNew],
  );
}
