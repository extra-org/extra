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

/** Every request names the conversation it belongs to.
 *
 * Storage only remembers which thread was selected last; it never decides where
 * an in-flight request lands. Switching threads mid-request would otherwise
 * redirect a reply that started in A into B.
 */
export interface Conversation {
  peekId(): string | null;
  ensureId(): Promise<string>;
  send(
    conversationId: string,
    text: string,
    onReplaced?: Retarget,
  ): Promise<SendMessageResponse>;
  stream(conversationId: string, text: string, onReplaced?: Retarget): AsyncGenerator<StreamEvent>;
  loadHistory(conversationId: string): Promise<ChatMessage[]>;
  loadUsage(conversationId: string): Promise<TokenBudget | null>;
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

/** Reports where a turn actually landed after its conversation vanished. The
 *  caller must write the rest of the turn to `freshId`, not the id it started
 *  with, or the answer is stored under a conversation that no longer exists. */
export type Retarget = (staleId: string, freshId: string) => void;

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

  const peekId = useCallback(() => getStoredConversationId(endpoint, userId), [endpoint, userId]);

  const ensureId = useCallback(
    async () => getStoredConversationId(endpoint, userId) ?? startConversation(),
    [endpoint, userId, startConversation],
  );

  const replace = useCallback(
    async (staleId: string, onReplaced?: Retarget) => {
      removeStoredConversationId(endpoint, userId);
      const fresh = await startConversation();
      onReplaced?.(staleId, fresh);
      return fresh;
    },
    [endpoint, userId, startConversation],
  );

  const send = useCallback(
    async (conversationId: string, text: string, onReplaced?: Retarget) => {
      try {
        return await client.sendMessage(conversationId, text);
      } catch (error) {
        if (!isUnusableConversation(error)) throw error;
        return client.sendMessage(await replace(conversationId, onReplaced), text);
      }
    },
    [client, replace],
  );

  const stream = useCallback(
    async function* (
      conversationId: string,
      text: string,
      onReplaced?: Retarget,
    ): AsyncGenerator<StreamEvent> {
      try {
        yield* client.streamMessage(conversationId, text);
      } catch (error) {
        if (!isUnusableConversation(error)) throw error;
        yield* client.streamMessage(await replace(conversationId, onReplaced), text);
      }
    },
    [client, replace],
  );

  const loadHistory = useCallback(
    async (conversationId: string) => {
      try {
        return await client.getMessages(conversationId);
      } catch (error) {
        if (isUnusableConversation(error) && peekId() === conversationId) {
          removeStoredConversationId(endpoint, userId);
        }
        return [];
      }
    },
    [client, endpoint, userId, peekId],
  );

  const loadUsage = useCallback(
    async (conversationId: string) => {
      try {
        return await client.getUsage(conversationId);
      } catch {
        return null;
      }
    },
    [client],
  );

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
