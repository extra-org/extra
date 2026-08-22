import {
  BotIcon,
  CheckIcon,
  ChevronDownIcon,
  CopyIcon,
  HistoryIcon,
  SquarePenIcon,
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
  const [threadsOpen, setThreadsOpen] = useState(false);
  const launcherRef = useRef<HTMLButtonElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const approvalRequestsRef = useRef(new Set<string>());
  const approvalCancellationRequestsRef = useRef(new Set<string>());
  const activeExecutionRef = useRef<ActiveExecution | null>(null);
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
      activeExecutionRef.current?.controller.abort();
    },
    [],
  );

  const beginExecution = useCallback((execution: ActiveExecution): boolean => {
    if (activeExecutionRef.current !== null) return false;
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
    setThreads(await conversation.listThreads());
    setThreadsOpen(true);
  }, [conversation]);

  // The generated title lands on the server sometime during (usually well
  // before) the turn's own stream finishes. Re-pull the list on every
  // completed turn, but only while a viewer could actually see it change.
  const refreshThreadsIfOpen = useCallback(async () => {
    if (!threadsOpen) return;
    setThreads(await conversation.listThreads());
  }, [conversation, threadsOpen]);

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
          completed ||= event.type === "final";
          entry = reduceStreamEvent(entry, event);
          replaceEntry(cid, pending.id, entry);
        }
        if (controller.signal.aborted && !completed) {
          replaceEntry(cid, pending.id, {
            id: pending.id,
            role: "ai",
            text: "",
            status: "cancelled",
          });
          return;
        }
        replaceEntry(cid, pending.id, { ...entry, typing: false });
        if (!entry.approval) {
          onAnswer({ visited: entry.route ?? [], used_tools: entry.tools ?? [] });
        }
      } catch (error) {
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
        finishExecution(controller);
        void refreshUsage(resolveConversationId(cid));
        void refreshThreadsIfOpen();
      }
    },
    [
      conversation,
      beginExecution,
      finishExecution,
      onAnswer,
      putEntries,
      refreshThreadsIfOpen,
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
            activeId={getStoredConversationId(config.endpoint)}
            onSelect={openThread}
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
                  onApproval={(approval, decision) =>
                    void decideApproval(activeId, entry, approval, decision)
                  }
                  onCancelApproval={(approval) =>
                    void cancelApproval(activeId, entry.id, approval)
                  }
                  onEdit={() => editMessage(entry)}
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
  onApproval,
  onCancelApproval,
  onEdit,
  editable,
}: {
  entry: MessageEntry;
  onApproval: (approval: PendingApproval, decision: ApprovalDecision) => void;
  onCancelApproval: (approval: PendingApproval) => void;
  onEdit: () => void;
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
          {entry.text.trim() ? <MessageActions text={entry.text} /> : null}
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

function MessageActions({ text }: { text: string }) {
  return (
    <div className="msg-actions">
      <CopyButton text={text} />
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
  onSelect,
  onNew,
  onClose,
}: {
  open: boolean;
  threads: ThreadSummary[];
  activeId: string | null;
  onSelect: (conversationId: string) => void;
  onNew: () => void;
  onClose: () => void;
}) {
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
      <div className="thread-list">
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
        {threads.length === 0 ? <p className="thread-empty">No conversations yet</p> : null}
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
