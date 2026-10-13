import {
  BotIcon,
  CheckIcon,
  ChevronDownIcon,
  CopyIcon,
  HistoryIcon,
  SquarePenIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
  XIcon,
} from "lucide-react";
import { type Ref, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AgentChatHttpError, type AgentChatClient } from "../api/AgentChatClient";
import { randomId } from "../id";
import { getStoredConversationId } from "../storage/conversationStorage";

import type {
  AgentChatAnswerDetail,
  AgentChatConfig,
  ApprovalDecision,
  ChatMessage,
  MessageEntry,
  PendingApproval,
  ThreadSummary,
  TokenBudget,
  ToolRecord,
} from "../types";
import {
  Conversation,
  ConversationContent,
  Message,
  MessageContent,
  MessageResponse,
  PromptInput,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
  Tool,
  ToolContent,
  ToolHeader,
  ToolOutput,
  type ToolState,
} from "./shadcnAiElements";
import { reduceStreamEvent } from "./streamReducer";
import { useConversation } from "./useConversation";

const THREADS_PAGE_SIZE = 20;
const SCROLL_THRESHOLD_PX = 40;

const DEFAULT_GREETING = "How can I help you today?";
const GENERIC_ERROR = "Something went wrong. Please try again.";
const COPIED_RESET_MS = 2000;

const isTerminalApprovalError = (error: unknown): error is AgentChatHttpError =>
  error instanceof AgentChatHttpError &&
  error.status >= 400 &&
  error.status < 500 &&
  ![408, 425, 429].includes(error.status);

const terminalApprovalMessage = (status: number): string => {
  if (status === 403) return "You are not authorized to decide this approval.";
  if (status === 409) return "This approval was already processed. You can continue chatting.";
  return "This approval is no longer available. You can continue chatting.";
};

const newId = randomId;

const toEntries = (message: ChatMessage): MessageEntry[] => {
  const entry: MessageEntry = {
    id: newId(),
    messageId: message.message_id,
    runId: message.run_id ?? undefined,
    role: message.role === "user" ? "user" : "ai",
    text: message.content,
    feedback: message.feedback,
  };
  return message.role === "user" && message.status === "cancelled"
    ? [entry, { id: newId(), role: "ai", text: "", status: "cancelled" }]
    : [entry];
};

interface ActiveExecution {
  controller: AbortController;
  runId?: string;
  source: "prompt" | "approval";
}

export interface AgentChatAppProps {
  client: AgentChatClient;
  config: AgentChatConfig;
  onAnswer: (detail: AgentChatAnswerDetail) => void;
  panelId: string;
  titleId: string;
}

export function AgentChatApp({
  client,
  config,
  onAnswer,
  panelId,
  titleId,
}: AgentChatAppProps) {
  const inline = config.mode === "inline";
  const [open, setOpen] = useState(inline);
  const [loaded, setLoaded] = useState(false);
  const [activeExecution, setActiveExecution] = useState<ActiveExecution | null>(null);
  const [budgetExceeded, setBudgetExceeded] = useState(false);
  const [entriesById, setEntriesById] = useState<Record<string, MessageEntry[]>>({});
  const [usageById, setUsageById] = useState<Record<string, TokenBudget | null>>({});
  const [activeId, setActiveId] = useState("");
  const [editing, setEditing] = useState<{ messageId: string; previousDraft: string } | null>(null);
  const entries = entriesById[activeId] ?? [];
  const usage = usageById[activeId] ?? null;
  const isExecutionActive = activeExecution !== null;
  const awaitingApproval = entries.some((entry) => entry.approval !== undefined);
  const approvalBlocksComposer = awaitingApproval && editing === null && !isExecutionActive;
  const canEdit = true;
  const canSubmit = !isExecutionActive && !budgetExceeded && !approvalBlocksComposer;
  const canStop = isExecutionActive;

  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMoreThreads, setLoadingMoreThreads] = useState(false);
  const [threadsError, setThreadsError] = useState<string | null>(null);
  const [threadsOpen, setThreadsOpen] = useState(false);
  const hasMoreThreads = nextCursor !== null;
  const isLoadingMoreRef = useRef(false);
  const threadsGenerationRef = useRef(0);
  const launcherRef = useRef<HTMLButtonElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const approvalRequestsRef = useRef(new Set<string>());
  const approvalCancellationRequestsRef = useRef(new Set<string>());
  const activeExecutionRef = useRef<ActiveExecution | null>(null);
  const requestControllersRef = useRef(new Set<AbortController>());
  const replacementIdsRef = useRef(new Map<string, string>());

  const resolveConversationId = useCallback((conversationId: string) => {
    let resolved = conversationId;
    while (replacementIdsRef.current.has(resolved)) {
      resolved = replacementIdsRef.current.get(resolved) as string;
    }
    return resolved;
  }, []);

  // A vanished conversation is replaced mid-turn; carry its messages onto the
  // id the turn actually ran under so the view does not go blank.
  const onReplaced = useCallback((staleId: string, freshId: string) => {
    replacementIdsRef.current.set(staleId, freshId);
    setEntriesById(({ [staleId]: moved = [], ...rest }) => ({ ...rest, [freshId]: moved }));
    setActiveId((current) => (current === staleId ? freshId : current));
  }, []);

  const conversation = useConversation(client, config.endpoint, onReplaced);

  useEffect(
    () => () => {
      for (const controller of requestControllersRef.current) controller.abort();
    },
    [],
  );

  const beginExecution = useCallback((execution: ActiveExecution): boolean => {
    if (activeExecutionRef.current !== null) return false;
    requestControllersRef.current.add(execution.controller);
    activeExecutionRef.current = execution;
    setActiveExecution(execution);
    return true;
  }, []);

  const finishExecution = useCallback((controller: AbortController) => {
    if (activeExecutionRef.current?.controller !== controller) return;
    activeExecutionRef.current = null;
    setActiveExecution(null);
  }, []);

  const refreshUsage = useCallback(
    async (cid: string) => {
      const next = await conversation.loadUsage(cid);
      setUsageById((prev) => ({ ...prev, [cid]: next }));
    },
    [conversation],
  );

  const putEntries = useCallback(
    (cid: string, update: (prev: MessageEntry[]) => MessageEntry[]) =>
      setEntriesById((prev) => ({ ...prev, [cid]: update(prev[cid] ?? []) })),
    [],
  );

  const loadThread = useCallback(
    async (cid: string) => {
      const history = await conversation.loadHistory(cid);
      putEntries(cid, () => history.flatMap(toEntries));
    },
    [conversation, putEntries],
  );

  const loadHistory = useCallback(async () => {
    if (loaded) return;
    setLoaded(true);
    const cid = conversation.peekId();
    if (!cid) return;
    setActiveId(cid);
    await loadThread(cid);
    await refreshUsage(cid);
  }, [conversation, loaded, loadThread, refreshUsage]);

  useEffect(() => {
    if (inline) void loadHistory();
  }, [inline, loadHistory]);

  useEffect(() => {
    if (open && !approvalBlocksComposer) inputRef.current?.focus({ preventScroll: true });
  }, [open, loaded, isExecutionActive, approvalBlocksComposer]);

  const openChat = useCallback(async () => {
    if (inline) return;
    setOpen(true);
    await loadHistory();
  }, [inline, loadHistory]);

  const closeChat = useCallback(() => {
    if (inline) return;
    setOpen(false);
    launcherRef.current?.focus({ preventScroll: true });
  }, [inline]);

  const openThreads = useCallback(async () => {
    threadsGenerationRef.current += 1;
    const currentGen = threadsGenerationRef.current;
    setThreadsOpen(true);
    setLoadingMoreThreads(true);
    setThreadsError(null);
    isLoadingMoreRef.current = true;
    try {
      const res = await conversation.listThreads(THREADS_PAGE_SIZE, null);
      if (threadsGenerationRef.current !== currentGen) return;
      setThreads(res.items);
      setNextCursor(res.next_cursor);
    } catch (err) {
      if (threadsGenerationRef.current !== currentGen) return;
      const msg = err instanceof AgentChatHttpError ? err.message : GENERIC_ERROR;
      setThreadsError(msg);
    } finally {
      if (threadsGenerationRef.current === currentGen) {
        setLoadingMoreThreads(false);
        isLoadingMoreRef.current = false;
      }
    }
  }, [conversation]);

  const loadMoreThreads = useCallback(async () => {
    if (isLoadingMoreRef.current || !nextCursor) return;
    const currentGen = threadsGenerationRef.current;
    isLoadingMoreRef.current = true;
    setLoadingMoreThreads(true);
    setThreadsError(null);
    try {
      const res = await conversation.listThreads(THREADS_PAGE_SIZE, nextCursor);
      if (threadsGenerationRef.current !== currentGen) return;
      setThreads((prev) => {
        // Keyset pagination sorts by (last_message_at, session_id). Since last_message_at
        // is mutable, a thread updated while scrolling could appear across page boundaries;
        // deduplication prevents duplicate items if order mutates mid-scroll.
        const existingIds = new Set(prev.map((t) => t.conversation_id));
        const newItems = res.items.filter((t) => !existingIds.has(t.conversation_id));
        return [...prev, ...newItems];
      });
      setNextCursor(res.next_cursor);
    } catch (err) {
      if (threadsGenerationRef.current !== currentGen) return;
      const msg = err instanceof AgentChatHttpError ? err.message : GENERIC_ERROR;
      setThreadsError(msg);
    } finally {
      if (threadsGenerationRef.current === currentGen) {
        setLoadingMoreThreads(false);
        isLoadingMoreRef.current = false;
      }
    }
  }, [conversation, nextCursor]);

  // The server sends a `title` event once generation actually finishes, so the
  // list is corrected from that event rather than from a guess about which
  // finished first — the turn or its title.
  const applyGeneratedTitle = useCallback((conversationId: string, title: string) => {
    setThreads((prev) =>
      prev.map((thread) =>
        thread.conversation_id === conversationId ? { ...thread, title } : thread,
      ),
    );
  }, []);

  const openThread = useCallback(
    async (conversationId: string) => {
      conversation.switchTo(conversationId);
      setThreadsOpen(false);
      setActiveId(conversationId);
      // ponytail: in-session map owns in-flight streams, so only cold-load a thread we haven't opened yet.
      if (!(conversationId in entriesById)) await loadThread(conversationId);
      await refreshUsage(conversationId);
      inputRef.current?.focus({ preventScroll: true });
    },
    [conversation, entriesById, loadThread, refreshUsage],
  );

  const startNewThread = useCallback(() => {
    conversation.startNew();
    setThreadsOpen(false);
    setActiveId("");
    inputRef.current?.focus({ preventScroll: true });
  }, [conversation]);

  const replaceEntry = useCallback(
    (cid: string, id: string, entry: MessageEntry) => {
      const resolvedId = resolveConversationId(cid);
      putEntries(resolvedId, (prev) =>
        prev.map((current) => (current.id === id ? entry : current)),
      );
    },
    [putEntries, resolveConversationId],
  );

  const stop = useCallback(() => {
    activeExecution?.controller.abort();
  }, [activeExecution]);

  const editMessage = useCallback((entry: MessageEntry) => {
    if (!entry.messageId || !inputRef.current) return;
    setEditing({ messageId: entry.messageId, previousDraft: inputRef.current.value });
    inputRef.current.value = entry.text;
    inputRef.current.dispatchEvent(new InputEvent("input", { bubbles: true }));
    inputRef.current.focus({ preventScroll: true });
  }, []);

  const cancelEditing = useCallback(() => {
    if (inputRef.current && editing) {
      inputRef.current.value = editing.previousDraft;
      inputRef.current.dispatchEvent(new InputEvent("input", { bubbles: true }));
      inputRef.current.focus({ preventScroll: true });
    }
    setEditing(null);
  }, [editing]);
  const decideApproval = useCallback(
    async (
      cid: string,
      initialEntry: MessageEntry,
      approval: PendingApproval,
      decision: ApprovalDecision,
    ) => {
      if (
        approvalRequestsRef.current.has(approval.approval_id) ||
        activeExecutionRef.current !== null
      ) {
        return;
      }
      const controller = new AbortController();
      if (
        !beginExecution({
          controller,
          runId: approval.run_id,
          source: "approval",
        })
      ) {
        return;
      }
      approvalRequestsRef.current.add(approval.approval_id);
      putEntries(cid, (prev) =>
        prev.map((entry) =>
          entry.id === initialEntry.id
            ? { ...entry, approvalSubmitting: true, approvalError: undefined }
            : entry,
        ),
      );
      let entry = initialEntry;
      let completed = false;
      let resumeStarted = false;
      try {
        for await (const event of conversation.streamApproval(
          cid,
          approval.run_id,
          approval.approval_id,
          decision,
          controller.signal,
        )) {
          if (controller.signal.aborted && event.type !== "final") break;
          resumeStarted ||= event.type === "resume_started";
          completed ||= event.type === "final";
          entry = reduceStreamEvent(entry, event);
          replaceEntry(cid, initialEntry.id, entry);
        }
        if (controller.signal.aborted && !completed) {
          if (!resumeStarted) {
            await conversation
              .cancelApproval(cid, approval.run_id, approval.approval_id)
              .catch(() => {});
          }
          replaceEntry(cid, initialEntry.id, {
            id: initialEntry.id,
            role: "ai",
            text: "",
            status: "cancelled",
          });
          return;
        }
        replaceEntry(cid, initialEntry.id, { ...entry, typing: false });
        if (completed && !entry.approval) {
          onAnswer({ visited: entry.route ?? [], used_tools: entry.tools ?? [] });
        }
      } catch (error) {
        if (controller.signal.aborted && !completed) {
          if (!resumeStarted) {
            await conversation
              .cancelApproval(cid, approval.run_id, approval.approval_id)
              .catch(() => {});
          }
          replaceEntry(cid, initialEntry.id, {
            id: initialEntry.id,
            role: "ai",
            text: "",
            status: "cancelled",
          });
          return;
        }
        if (completed) {
          replaceEntry(cid, initialEntry.id, { ...entry, typing: false });
          onAnswer({ visited: entry.route ?? [], used_tools: entry.tools ?? [] });
          return;
        }
        if (resumeStarted) {
          replaceEntry(cid, initialEntry.id, {
            id: initialEntry.id,
            role: "ai",
            text: GENERIC_ERROR,
            error: true,
          });
          return;
        }
        const terminal = isTerminalApprovalError(error);
        putEntries(cid, (prev) =>
          prev.map((entry) =>
            entry.id === initialEntry.id &&
            entry.approval?.approval_id === approval.approval_id &&
            !entry.approvalCancelling
              ? {
                  ...entry,
                  ...(terminal
                    ? {
                        text: terminalApprovalMessage(error.status),
                        error: true,
                        approval: undefined,
                      }
                    : {}),
                  approvalSubmitting: false,
                  approvalError: terminal ? undefined : GENERIC_ERROR,
                }
              : entry,
          ),
        );
      } finally {
        approvalRequestsRef.current.delete(approval.approval_id);
        requestControllersRef.current.delete(controller);
        finishExecution(controller);
        void refreshUsage(cid);
      }
    },
    [
      beginExecution,
      conversation,
      finishExecution,
      onAnswer,
      putEntries,
      refreshUsage,
      replaceEntry,
    ],
  );

  const cancelApproval = useCallback(
    async (cid: string, entryId: string, approval: PendingApproval) => {
      if (approvalCancellationRequestsRef.current.has(approval.approval_id)) return;
      approvalCancellationRequestsRef.current.add(approval.approval_id);
      putEntries(cid, (prev) =>
        prev.map((entry) =>
          entry.id === entryId
            ? { ...entry, approvalCancelling: true, approvalError: undefined }
            : entry,
        ),
      );
      try {
        await conversation.cancelApproval(cid, approval.run_id, approval.approval_id);
        putEntries(cid, (prev) =>
          prev.map((entry) =>
            entry.id === entryId
              ? { id: entry.id, role: "ai", text: "", status: "cancelled" }
              : entry,
          ),
        );
      } catch (error) {
        const terminal = isTerminalApprovalError(error);
        putEntries(cid, (prev) =>
          prev.map((entry) =>
            entry.id === entryId && entry.approval?.approval_id === approval.approval_id
              ? {
                  ...entry,
                  ...(terminal
                    ? {
                        text: terminalApprovalMessage(error.status),
                        error: true,
                        approval: undefined,
                      }
                    : {}),
                  approvalCancelling: false,
                  approvalError: terminal ? undefined : GENERIC_ERROR,
                }
              : entry,
          ),
        );
      } finally {
        approvalCancellationRequestsRef.current.delete(approval.approval_id);
        void refreshUsage(cid);
      }
    },
    [conversation, putEntries, refreshUsage],
  );

  const submit = useCallback(
    async (text: string, editMessageId?: string) => {
      if (activeExecutionRef.current !== null) return;
      const cid = await conversation.ensureId();
      setActiveId(cid);
      let userEntry: MessageEntry = { id: newId(), role: "user", text };
      const pending: MessageEntry = { id: newId(), role: "ai", text: "", typing: true };
      const controller = new AbortController();
      if (!beginExecution({ controller, source: "prompt" })) return;
      putEntries(cid, (prev) => {
        if (!editMessageId) return [...prev, userEntry, pending];
        const branchPoint = prev.findIndex((entry) => entry.messageId === editMessageId);
        return [...(branchPoint < 0 ? prev : prev.slice(0, branchPoint)), userEntry, pending];
      });
      setEditing(null);
      let entry = pending;
      let completed = false;
      let executionSettled = false;
      try {
        for await (const event of conversation.stream(
          cid,
          text,
          controller.signal,
          editMessageId,
        )) {
          if (controller.signal.aborted && event.type !== "final") break;
          if (event.type === "turn_started") {
            userEntry = {
              ...userEntry,
              messageId: event.message_id,
              runId: event.run_id,
            };
            replaceEntry(cid, userEntry.id, userEntry);
            continue;
          }
          if (event.type === "title") {
            if (event.title) applyGeneratedTitle(resolveConversationId(cid), event.title);
            continue;
          }
          completed ||= event.type === "final";
          entry = reduceStreamEvent(entry, event);
          replaceEntry(cid, pending.id, entry);
          if (event.type === "final" || event.type === "pending_approval") {
            executionSettled = true;
            replaceEntry(cid, pending.id, { ...entry, typing: false });
            if (event.type === "final") {
              onAnswer({ visited: entry.route ?? [], used_tools: entry.tools ?? [] });
            }
            // The title event may arrive later on this response. It is
            // secondary work: release the composer at the main terminal event
            // while continuing to consume the stream for that update.
            finishExecution(controller);
            void refreshUsage(resolveConversationId(cid));
          }
        }
        if (!executionSettled && controller.signal.aborted && !completed) {
          replaceEntry(cid, pending.id, {
            id: pending.id,
            role: "ai",
            text: "",
            status: "cancelled",
          });
          return;
        }
        if (!executionSettled) {
          replaceEntry(cid, pending.id, { ...entry, typing: false });
          if (!entry.approval) {
            onAnswer({ visited: entry.route ?? [], used_tools: entry.tools ?? [] });
          }
        }
      } catch (error) {
        if (executionSettled) return;
        if (controller.signal.aborted && !completed) {
          replaceEntry(cid, pending.id, {
            id: pending.id,
            role: "ai",
            text: "",
            status: "cancelled",
          });
          return;
        }
        const is4xx = error instanceof AgentChatHttpError && error.status >= 400 && error.status < 500;
        if (error instanceof AgentChatHttpError && error.errorType === "context_limit_exceeded") {
          setBudgetExceeded(true);
        }
        if (completed) {
          replaceEntry(cid, pending.id, { ...entry, typing: false });
          onAnswer({ visited: entry.route ?? [], used_tools: entry.tools ?? [] });
        } else if (entry.approval) {
          replaceEntry(cid, pending.id, { ...entry, typing: false });
        } else {
          const message = is4xx ? error.message : GENERIC_ERROR;
          replaceEntry(cid, pending.id, { id: pending.id, role: "ai", text: message, error: true });
        }
      } finally {
        requestControllersRef.current.delete(controller);
        finishExecution(controller);
        if (!executionSettled) void refreshUsage(resolveConversationId(cid));
      }
    },
    [
      conversation,
      beginExecution,
      finishExecution,
      onAnswer,
      putEntries,
      applyGeneratedTitle,
      refreshUsage,
      replaceEntry,
      resolveConversationId,
    ],
  );

  const toggle = () => void (open ? closeChat() : openChat());

  return (
    <div
      className="agent-chat-react"
      onKeyDown={(event) => {
        if (event.key === "Escape" && !inline && open) {
          event.preventDefault();
          closeChat();
        }
        event.stopPropagation();
      }}
      onKeyPress={(event) => event.stopPropagation()}
      onKeyUp={(event) => event.stopPropagation()}
    >
      {!inline ? (
        <Launcher open={open} panelId={panelId} buttonRef={launcherRef} onToggle={toggle} />
      ) : null}

      <section
        aria-labelledby={titleId}
        className={`panel${inline ? " inline" : ""}${open && !inline ? " open" : ""}`}
        id={panelId}
        role={inline ? "region" : "dialog"}
      >
        <header className="header">
          <button
            aria-label="Conversations"
            className="header-btn"
            onClick={() => void openThreads()}
            type="button"
          >
            <HistoryIcon aria-hidden />
          </button>
          <span className="dot" style={avatarStyle(config.avatar)} />
          <span className="title" id={titleId}>
            {config.title}
          </span>
          <button
            aria-label="New chat"
            className="header-btn"
            onClick={startNewThread}
            type="button"
          >
            <SquarePenIcon aria-hidden />
          </button>
          {!inline ? (
            <button aria-label="Close chat" className="close" onClick={closeChat} type="button">
              <XIcon aria-hidden />
            </button>
          ) : null}
        </header>

        <div className="body">
          <ThreadDrawer
            open={threadsOpen}
            threads={threads}
            activeId={activeId}
            loadingMore={loadingMoreThreads}
            error={threadsError}
            hasMore={hasMoreThreads}
            onLoadMore={() => void loadMoreThreads()}
            onRetry={() => (threads.length === 0 ? void openThreads() : void loadMoreThreads())}
            onSelect={(cid) => void openThread(cid)}
            onNew={startNewThread}
            onClose={() => setThreadsOpen(false)}
          />
          <Conversation>
            <ConversationContent>
              {entries.length === 0 ? (
                <Welcome title={config.greeting || DEFAULT_GREETING} />
              ) : null}
              {entries.map((entry) => (
                <ChatMessage
                  key={entry.id}
                  entry={entry}
                  conversationId={activeId}
                  onApproval={(approval, decision) =>
                    void decideApproval(activeId, entry, approval, decision)
                  }
                  onCancelApproval={(approval) =>
                    void cancelApproval(activeId, entry.id, approval)
                  }
                  onEdit={() => editMessage(entry)}
                  onFeedback={(messageId, feedback) =>
                    void conversation.setMessageFeedback(activeId, messageId, feedback)
                  }
                  editable={canEdit}
                />
              ))}
            </ConversationContent>
          </Conversation>
          <PromptInput
            submitEnabled={canSubmit}
            onSubmit={(message) => void submit(message.text, editing?.messageId)}
          >
            <PromptInputTextarea
              aria-label="Message"
              disabled={budgetExceeded || approvalBlocksComposer}
              inputRef={inputRef}
              onSubmit={() => inputRef.current?.form?.requestSubmit()}
              placeholder={
                budgetExceeded
                  ? "Context limit reached."
                  : approvalBlocksComposer
                    ? "Respond to the approval request above."
                    : "Message..."
              }
            />
            <PromptInputFooter>
              <div className="footer-start">
                {usage ? <BudgetMeter usage={usage} /> : null}
                {editing ? (
                  <button className="edit-cancel" onClick={cancelEditing} type="button">
                    Cancel edit
                  </button>
                ) : null}
                <span className="prompt-hint">Enter to send · Shift+Enter for a new line</span>
              </div>
              <PromptInputSubmit
                disabled={!canSubmit && !canStop}
                running={canStop}
                onStop={stop}
              />
            </PromptInputFooter>
          </PromptInput>
          <div className="powered">Powered by Extra</div>
        </div>
      </section>
    </div>
  );
}

function Launcher({
  open,
  panelId,
  buttonRef,
  onToggle,
}: {
  open: boolean;
  panelId: string;
  buttonRef: Ref<HTMLButtonElement>;
  onToggle: () => void;
}) {
  return (
    <button
      aria-controls={panelId}
      aria-expanded={open}
      aria-label={open ? "Close Assistant" : "Open Assistant"}
      className={`launcher${open ? " open" : ""}`}
      onClick={onToggle}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onToggle();
        }
      }}
      ref={buttonRef}
      type="button"
    >
      <BotIcon className="icon-bot" aria-hidden />
      <ChevronDownIcon className="icon-chevron" aria-hidden />
    </button>
  );
}

function ChatMessage({
  entry,
  conversationId,
  onApproval,
  onCancelApproval,
  onEdit,
  onFeedback,
  editable,
}: {
  entry: MessageEntry;
  conversationId?: string;
  onApproval: (approval: PendingApproval, decision: ApprovalDecision) => void;
  onCancelApproval: (approval: PendingApproval) => void;
  onEdit: () => void;
  onFeedback: (messageId: string, feedback: "thumbs_up" | "thumbs_down") => void;
  editable: boolean;
}) {
  const from = entry.role === "user" ? "user" : "assistant";

  if (entry.error) {
    return (
      <Message from={from}>
        <div className="msg-error" role="alert">
          {entry.text}
        </div>
      </Message>
    );
  }

  if (entry.status === "cancelled") {
    return (
      <Message from="assistant">
        <div className="msg-cancelled" role="status">
          Generation stopped
        </div>
      </Message>
    );
  }

  if (entry.role === "user") {
    return (
      <Message from="user">
        <MessageContent>{entry.text}</MessageContent>
        {editable && entry.messageId ? (
          <div className="msg-actions user-actions">
            <button aria-label="Edit message" className="user-edit" onClick={onEdit} type="button">
              Edit
            </button>
          </div>
        ) : null}
      </Message>
    );
  }

  const thinking = Boolean(entry.typing) && !entry.text.trim();

  return (
    <Message from="assistant" typing={thinking}>
      <AgentActivity route={entry.route} tools={entry.tools} />
      {thinking ? (
        <ThinkingDots />
      ) : (
        <>
          {entry.approval ? (
            <ApprovalRequest
              approval={entry.approval}
              error={entry.approvalError}
              cancelling={Boolean(entry.approvalCancelling)}
              submitting={Boolean(entry.approvalSubmitting)}
              onDecision={(decision) => onApproval(entry.approval as PendingApproval, decision)}
              onCancel={() => onCancelApproval(entry.approval as PendingApproval)}
            />
          ) : null}
          {entry.text.trim() ? (
            <MessageContent>
              <MessageResponse>{entry.text}</MessageResponse>
            </MessageContent>
          ) : null}
          {entry.text.trim() ? <MessageActions text={entry.text} feedback={entry.feedback} messageId={entry.messageId} conversationId={conversationId} onFeedback={onFeedback} /> : null}
        </>
      )}
    </Message>
  );
}

function ApprovalRequest({
  approval,
  submitting,
  cancelling,
  error,
  onDecision,
  onCancel,
}: {
  approval: PendingApproval;
  submitting: boolean;
  cancelling: boolean;
  error?: string;
  onDecision: (decision: ApprovalDecision) => void;
  onCancel: () => void;
}) {
  return (
    <section
      className="approval-card"
      aria-busy={submitting || cancelling}
      aria-label="Tool approval request"
    >
      <p className="approval-title">Approval required</p>
      <p className="approval-description">{approval.description}</p>
      <p className="approval-tool">Tool: {approval.tool_name}</p>
      {error ? (
        <p className="approval-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="approval-actions">
        <button
          className="approval-button primary"
          disabled={submitting || cancelling}
          onClick={() => onDecision("allow_once")}
          type="button"
        >
          Approve
        </button>
        <button
          className="approval-button danger"
          disabled={submitting || cancelling}
          onClick={() => onDecision("deny")}
          type="button"
        >
          Deny
        </button>
        <button
          className="approval-button"
          disabled={submitting || cancelling}
          onClick={() => onDecision("allow_for_session")}
          type="button"
        >
          Approve for this session
        </button>
        <button
          className="approval-button cancel-run"
          disabled={cancelling}
          onClick={onCancel}
          type="button"
        >
          Cancel run
        </button>
      </div>
      {cancelling ? <span className="approval-status">Cancelling run…</span> : null}
      {!cancelling && submitting ? (
        <span className="approval-status">Applying decision…</span>
      ) : null}
    </section>
  );
}

function ThinkingDots() {
  return (
    <span className="thinking" aria-hidden>
      <span className="thinking-dot" />
      <span className="thinking-dot" />
      <span className="thinking-dot" />
    </span>
  );
}

function MessageActions({
  text,
  feedback,
  messageId,
  conversationId,
  onFeedback,
}: {
  text: string;
  feedback?: "thumbs_up" | "thumbs_down";
  messageId?: string;
  conversationId?: string;
  onFeedback?: (messageId: string, feedback: "thumbs_up" | "thumbs_down") => void;
}) {
  const handleThumbsUp = useCallback(() => {
    if (!messageId || !conversationId || !onFeedback) return;
    const next = feedback === "thumbs_up" ? null : "thumbs_up";
    if (next) {
      void onFeedback(messageId, next);
    }
  }, [messageId, conversationId, onFeedback, feedback]);

  const handleThumbsDown = useCallback(() => {
    if (!messageId || !conversationId || !onFeedback) return;
    const next = feedback === "thumbs_down" ? null : "thumbs_down";
    if (next) {
      void onFeedback(messageId, next);
    }
  }, [messageId, conversationId, onFeedback, feedback]);

  return (
    <div className="msg-actions">
      <CopyButton text={text} />
      {messageId && conversationId && onFeedback ? (
        <div className="feedback-actions">
          <button
            aria-label="Thumbs up"
            aria-pressed={feedback === "thumbs_up"}
            className={`msg-action feedback-up${feedback === "thumbs_up" ? " active" : ""}`}
            onClick={handleThumbsUp}
            type="button"
          >
            <ThumbsUpIcon aria-hidden />
          </button>
          <button
            aria-label="Thumbs down"
            aria-pressed={feedback === "thumbs_down"}
            className={`msg-action feedback-down${feedback === "thumbs_down" ? " active" : ""}`}
            onClick={handleThumbsDown}
            type="button"
          >
            <ThumbsDownIcon aria-hidden />
          </button>
        </div>
      ) : null}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const copy = useCallback(() => {
    void navigator.clipboard
      ?.writeText(text)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), COPIED_RESET_MS);
      })
      .catch(() => {});
  }, [text]);

  const Icon = copied ? CheckIcon : CopyIcon;
  return (
    <button aria-label={copied ? "Copied" : "Copy"} className="msg-action" onClick={copy} type="button">
      <Icon aria-hidden />
    </button>
  );
}

function AgentActivity({ route, tools = [] }: { route?: string[]; tools?: ToolRecord[] }) {
  if (!route?.length && tools.length === 0) return null;
  return (
    <div className="tool-list">
      {route?.length ? (
        <div className="route" aria-label="Agent route">
          {route.join(" -> ")}
        </div>
      ) : null}
      {tools.map((tool, index) => (
        <Tool key={`${tool.name}-${index}`} defaultOpen={tool.status === "failed"}>
          <ToolHeader
            state={toToolState(tool.status)}
            title={tool.name}
          />
          {tool.error ? (
            <ToolContent>
              <ToolOutput errorText={tool.error} />
            </ToolContent>
          ) : null}
        </Tool>
      ))}
    </div>
  );
}

function Welcome({ title }: { title: string }) {
  return (
    <div className="welcome">
      <span className="welcome-avatar">
        <BotIcon aria-hidden />
      </span>
      <p className="welcome-title">{title}</p>
    </div>
  );
}

function ThreadDrawer({
  open,
  threads,
  activeId,
  loadingMore,
  error,
  hasMore,
  onLoadMore,
  onRetry,
  onSelect,
  onNew,
  onClose,
}: {
  open: boolean;
  threads: ThreadSummary[];
  activeId: string | null;
  loadingMore: boolean;
  error: string | null;
  hasMore: boolean;
  onLoadMore: () => void;
  onRetry: () => void;
  onSelect: (conversationId: string) => void;
  onNew: () => void;
  onClose: () => void;
}) {
  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const { scrollTop, clientHeight, scrollHeight } = e.currentTarget;
    if (scrollHeight - scrollTop - clientHeight < SCROLL_THRESHOLD_PX && hasMore && !loadingMore && !error) {
      onLoadMore();
    }
  };

  return (
    <div className={`thread-drawer${open ? " open" : ""}`} inert={!open}>
      <div className="thread-drawer-head">
        <span>Chats</span>
        <button aria-label="Close conversations" className="header-btn" onClick={onClose} type="button">
          <XIcon aria-hidden />
        </button>
      </div>
      <button className="thread-new" onClick={onNew} type="button">
        <SquarePenIcon aria-hidden />
        New chat
      </button>
      <div className="thread-list" onScroll={handleScroll}>
        {threads.map((thread) => (
          <button
            key={thread.conversation_id}
            className={`thread-item${thread.conversation_id === activeId ? " active" : ""}`}
            aria-current={thread.conversation_id === activeId}
            onClick={() => onSelect(thread.conversation_id)}
            type="button"
          >
            {thread.title || "New chat"}
          </button>
        ))}
        {threads.length === 0 && !loadingMore && !error ? <p className="thread-empty">No conversations yet</p> : null}
        {error ? (
          <div className="thread-empty thread-error">
            <p>{error}</p>
            <button className="thread-retry-btn" onClick={onRetry} type="button">
              Retry
            </button>
          </div>
        ) : null}
        {loadingMore ? <p className="thread-empty">Loading...</p> : null}
        {hasMore && !loadingMore && !error ? (
          <button className="thread-load-more-btn" onClick={onLoadMore} type="button">
            Load more
          </button>
        ) : null}
      </div>
    </div>
  );
}

const RING_SIZE = 18;
const RING_STROKE = 2.5;
const RING_RADIUS = (RING_SIZE - RING_STROKE) / 2;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

/** Cumulative tokens this conversation has spent, not the current context size. */
function BudgetMeter({ usage }: { usage: TokenBudget }) {
  const { used_tokens: used, max_tokens: max, percent, severity } = usage;
  if (!max) return null;

  const center = RING_SIZE / 2;
  const ring = { cx: center, cy: center, r: RING_RADIUS, fill: "none", strokeWidth: RING_STROKE };
  const rounded = Math.round(percent);

  return (
    <span
      className={`budget-meter ${severity}`}
      role="img"
      aria-label={`Token budget ${rounded}% used`}
      tabIndex={0}
    >
      <svg
        className="budget-ring"
        width={RING_SIZE}
        height={RING_SIZE}
        viewBox={`0 0 ${RING_SIZE} ${RING_SIZE}`}
        aria-hidden
      >
        <circle className="budget-ring-track" {...ring} />
        <circle
          className="budget-ring-value"
          {...ring}
          strokeLinecap="round"
          strokeDasharray={RING_CIRCUMFERENCE}
          strokeDashoffset={RING_CIRCUMFERENCE * (1 - percent / 100)}
        />
      </svg>
      <span className="budget-percent">{rounded}%</span>
      <span className="budget-popover" role="tooltip">
        <span className="budget-popover-head">
          <span>Token budget</span>
          <span className="budget-popover-count">
            {formatTokens(Math.min(used, max))} of {formatTokens(max)}
          </span>
        </span>
        <span className="budget-bar">
          <span className="budget-bar-fill" style={{ width: `${percent}%` }} />
        </span>
      </span>
    </span>
  );
}

function formatTokens(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1).replace(/\.0$/, "")}k`;
  return `${tokens}`;
}

function avatarStyle(avatar: string) {
  if (!avatar) return undefined;
  return { backgroundImage: `url("${avatar.replace(/"/g, "%22")}")` };
}

function toToolState(status: string): ToolState {
  if (status === "failed") return "output-error";
  if (status === "succeeded") return "output-available";
  return "input-available";
}
