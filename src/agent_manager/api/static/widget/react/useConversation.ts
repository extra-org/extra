import { useCallback, useMemo } from "react";

import { AgentChatHttpError, type AgentChatClient } from "../api/AgentChatClient";
import {
  getStoredConversationId,
  removeStoredConversationId,
  setStoredConversationId,
} from "../storage/conversationStorage";
import type {
  ApprovalDecision,
  ChatMessage,
  PaginatedThreads,
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
    signal?: AbortSignal,
    editMessageId?: string,
  ): Promise<SendMessageResponse>;
  stream(
    conversationId: string,
    text: string,
    signal?: AbortSignal,
    editMessageId?: string,
  ): AsyncGenerator<StreamEvent>;
  decideApproval(
    conversationId: string,
    runId: string,
    approvalId: string,
    decision: ApprovalDecision,
  ): Promise<SendMessageResponse>;
  streamApproval(
    conversationId: string,
    runId: string,
    approvalId: string,
    decision: ApprovalDecision,
    signal?: AbortSignal,
  ): AsyncGenerator<StreamEvent>;
  cancelApproval(conversationId: string, runId: string, approvalId: string): Promise<void>;
  loadHistory(conversationId: string): Promise<ChatMessage[]>;
  loadUsage(conversationId: string): Promise<TokenBudget | null>;
  listThreads(limit?: number, cursor?: string | null): Promise<PaginatedThreads>;
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
  /** Told when a vanished conversation is replaced, so the caller can move that
   *  thread's messages onto the id the turn actually ran under. */
  onReplaced?: (staleId: string, freshId: string) => void,
): Conversation {
  const startConversation = useCallback(async () => {
    const created = await client.createConversation();
    setStoredConversationId(endpoint, created);
    return created;
  }, [client, endpoint]);

  const peekId = useCallback(() => getStoredConversationId(endpoint), [endpoint]);

  const ensureId = useCallback(
    async () => getStoredConversationId(endpoint) ?? startConversation(),
    [endpoint, startConversation],
  );

  const replace = useCallback(
    async (staleId: string) => {
      removeStoredConversationId(endpoint);
      const fresh = await startConversation();
      onReplaced?.(staleId, fresh);
      return fresh;
    },
    [endpoint, startConversation, onReplaced],
  );

  const send = useCallback(
    async (
      conversationId: string,
      text: string,
      signal?: AbortSignal,
      editMessageId?: string,
    ) => {
      try {
        return await client.sendMessage(conversationId, text, signal, editMessageId);
      } catch (error) {
        if (!isUnusableConversation(error)) throw error;
        // A replacement conversation cannot contain a message id from the
        // vanished one. Preserve the edited text, but make it the fresh root.
        return client.sendMessage(await replace(conversationId), text, signal);
      }
    },
    [client, replace],
  );

  const stream = useCallback(
    async function* (
      conversationId: string,
      text: string,
      signal?: AbortSignal,
      editMessageId?: string,
    ): AsyncGenerator<StreamEvent> {
      try {
        yield* client.streamMessage(conversationId, text, signal, editMessageId);
      } catch (error) {
        if (!isUnusableConversation(error)) throw error;
        // The old branch no longer exists server-side, so retrying its edit id
        // against the newly created conversation would deterministically 404.
        yield* client.streamMessage(await replace(conversationId), text, signal);
      }
    },
    [client, replace],
  );

  const decideApproval = useCallback(
    (
      conversationId: string,
      runId: string,
      approvalId: string,
      decision: ApprovalDecision,
    ) => client.decideApproval(conversationId, runId, approvalId, decision),
    [client],
  );

  const streamApproval = useCallback(
    (
      conversationId: string,
      runId: string,
      approvalId: string,
      decision: ApprovalDecision,
      signal?: AbortSignal,
    ) => client.streamApproval(conversationId, runId, approvalId, decision, signal),
    [client],
  );

  const cancelApproval = useCallback(
    (conversationId: string, runId: string, approvalId: string) =>
      client.cancelApproval(conversationId, runId, approvalId),
    [client],
  );
  const loadHistory = useCallback(
    async (conversationId: string) => {
      try {
        return await client.getMessages(conversationId);
      } catch (error) {
        if (isUnusableConversation(error) && peekId() === conversationId) {
          removeStoredConversationId(endpoint);
        }
        return [];
      }
    },
    [client, endpoint, peekId],
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

  const listThreads = useCallback(
    (limit?: number, cursor?: string | null) =>
      client.listConversations(limit, cursor).catch(() => ({ items: [], next_cursor: null })),
    [client],
  );

  const switchTo = useCallback(
    (conversationId: string) => setStoredConversationId(endpoint, conversationId),
    [endpoint],
  );

  const startNew = useCallback(
    () => removeStoredConversationId(endpoint),
    [endpoint],
  );

  return useMemo(
    () => ({
      peekId,
      ensureId,
      send,
      stream,
      decideApproval,
      streamApproval,
      cancelApproval,
      loadHistory,
      loadUsage,
      listThreads,
      switchTo,
      startNew,
    }),
    [
      peekId,
      ensureId,
      send,
      stream,
      decideApproval,
      streamApproval,
      cancelApproval,
      loadHistory,
      loadUsage,
      listThreads,
      switchTo,
      startNew,
    ],
  );
}
