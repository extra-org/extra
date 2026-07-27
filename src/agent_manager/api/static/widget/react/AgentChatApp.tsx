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

import type { AgentChatClient } from "../api/AgentChatClient";
import { getOrCreateUserId, getStoredConversationId } from "../storage/conversationStorage";
import type {
  AgentChatAnswerDetail,
  AgentChatConfig,
  ChatMessage,
  ContextUsage,
  MessageEntry,
  ThreadSummary,
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

const newId = () => crypto.randomUUID();

const toEntry = (message: ChatMessage): MessageEntry => ({
  id: newId(),
  role: message.role === "user" ? "user" : "ai",
  text: message.content,
});

export interface AgentChatAppProps {
  client: AgentChatClient;
  config: AgentChatConfig;
  onAnswer: (detail: AgentChatAnswerDetail) => void;
  panelId: string;
  titleId: string;
}

export function AgentChatApp({ client, config, onAnswer, panelId, titleId }: AgentChatAppProps) {
  const inline = config.mode === "inline";
  const userId = useMemo(
    () => config.user || getOrCreateUserId(config.endpoint),
    [config.user, config.endpoint],
  );
  const conversation = useConversation(client, config.endpoint, userId);
  const [open, setOpen] = useState(inline);
  const [loaded, setLoaded] = useState(false);
  const [sending, setSending] = useState(false);
  const [entriesById, setEntriesById] = useState<Record<string, MessageEntry[]>>({});
  const [activeId, setActiveId] = useState("");
  const entries = entriesById[activeId] ?? [];
  const [usage, setUsage] = useState<ContextUsage | null>(null);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [threadsOpen, setThreadsOpen] = useState(false);
  const launcherRef = useRef<HTMLButtonElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  const refreshUsage = useCallback(async () => {
    setUsage(await conversation.loadUsage());
  }, [conversation]);

  const putEntries = useCallback(
    (cid: string, update: (prev: MessageEntry[]) => MessageEntry[]) =>
      setEntriesById((prev) => ({ ...prev, [cid]: update(prev[cid] ?? []) })),
    [],
  );

  const loadThread = useCallback(
    async (cid: string) => {
      const history = await conversation.loadHistory();
      putEntries(cid, () => history.map(toEntry));
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
    await refreshUsage();
  }, [conversation, loaded, loadThread, refreshUsage]);

  useEffect(() => {
    if (inline) void loadHistory();
  }, [inline, loadHistory]);

  useEffect(() => {
    if (open) inputRef.current?.focus({ preventScroll: true });
  }, [open, loaded, sending]);

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

  const openThread = useCallback(
    async (conversationId: string) => {
      conversation.switchTo(conversationId);
      setThreadsOpen(false);
      setActiveId(conversationId);
      // ponytail: in-session map owns in-flight streams, so only cold-load a thread we haven't opened yet.
      if (!(conversationId in entriesById)) await loadThread(conversationId);
      await refreshUsage();
      inputRef.current?.focus({ preventScroll: true });
    },
    [conversation, entriesById, loadThread, refreshUsage],
  );

  const startNewThread = useCallback(() => {
    conversation.startNew();
    setThreadsOpen(false);
    setActiveId("");
    setUsage(null);
    inputRef.current?.focus({ preventScroll: true });
  }, [conversation]);

  const replaceEntry = useCallback(
    (cid: string, id: string, entry: MessageEntry) =>
      putEntries(cid, (prev) => prev.map((current) => (current.id === id ? entry : current))),
    [putEntries],
  );

  const sendWithoutStreaming = useCallback(
    async (cid: string, text: string, entryId: string) => {
      try {
        const answer = await conversation.send(text);
        replaceEntry(cid, entryId, {
          id: entryId,
          role: "ai",
          text: answer.answer,
          route: answer.visited,
          tools: answer.used_tools,
        });
        onAnswer({ visited: answer.visited ?? [], used_tools: answer.used_tools ?? [] });
      } catch {
        replaceEntry(cid, entryId, { id: entryId, role: "ai", text: GENERIC_ERROR, error: true });
      }
    },
    [conversation, onAnswer, replaceEntry],
  );

  const submit = useCallback(
    async (text: string) => {
      const cid = await conversation.ensureId();
      setActiveId(cid);
      const pending: MessageEntry = { id: newId(), role: "ai", text: "", typing: true };
      putEntries(cid, (prev) => [...prev, { id: newId(), role: "user", text }, pending]);
      setSending(true);
      try {
        let entry = pending;
        for await (const event of conversation.stream(text)) {
          entry = reduceStreamEvent(entry, event);
          replaceEntry(cid, pending.id, entry);
        }
        replaceEntry(cid, pending.id, { ...entry, typing: false });
        onAnswer({ visited: entry.route ?? [], used_tools: entry.tools ?? [] });
      } catch {
        await sendWithoutStreaming(cid, text, pending.id);
      } finally {
        setSending(false);
        void refreshUsage();
      }
    },
    [conversation, onAnswer, putEntries, refreshUsage, replaceEntry, sendWithoutStreaming],
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
                <ChatMessage key={entry.id} entry={entry} />
              ))}
            </ConversationContent>
          </Conversation>
          <PromptInput onSubmit={(message) => void submit(message.text)}>
            <PromptInputTextarea
              aria-label="Message"
              disabled={false}
              inputRef={inputRef}
              onSubmit={() => inputRef.current?.form?.requestSubmit()}
              placeholder="Message..."
            />
            <PromptInputFooter>
              <div className="footer-start">
                {usage ? <ContextMeter usage={usage} /> : null}
                <span className="prompt-hint">Enter to send · Shift+Enter for a new line</span>
              </div>
              <PromptInputSubmit disabled={sending} />
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

function ChatMessage({ entry }: { entry: MessageEntry }) {
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

  if (entry.role === "user") {
    return (
      <Message from="user">
        <MessageContent>{entry.text}</MessageContent>
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
          <MessageContent>
            <MessageResponse>{entry.text}</MessageResponse>
          </MessageContent>
          {entry.text.trim() ? <MessageActions text={entry.text} /> : null}
        </>
      )}
    </Message>
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
            title={tool.provider ? `${tool.name} · ${tool.provider}` : tool.name}
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

function ContextMeter({ usage }: { usage: ContextUsage }) {
  const { used_tokens: used, max_tokens: max, percent, severity } = usage;
  if (!max) return null;

  const center = RING_SIZE / 2;
  const ring = { cx: center, cy: center, r: RING_RADIUS, fill: "none", strokeWidth: RING_STROKE };
  const rounded = Math.round(percent);

  return (
    <span
      className={`context-meter ${severity}`}
      role="img"
      aria-label={`Context ${rounded}% used`}
      tabIndex={0}
    >
      <svg
        className="context-ring"
        width={RING_SIZE}
        height={RING_SIZE}
        viewBox={`0 0 ${RING_SIZE} ${RING_SIZE}`}
        aria-hidden
      >
        <circle className="context-ring-track" {...ring} />
        <circle
          className="context-ring-value"
          {...ring}
          strokeLinecap="round"
          strokeDasharray={RING_CIRCUMFERENCE}
          strokeDashoffset={RING_CIRCUMFERENCE * (1 - percent / 100)}
        />
      </svg>
      <span className="context-percent">{rounded}%</span>
      <span className="context-popover" role="tooltip">
        <span className="context-popover-head">
          <span>Context usage</span>
          <span className="context-popover-count">
            {formatTokens(Math.min(used, max))} of {formatTokens(max)}
          </span>
        </span>
        <span className="context-bar">
          <span className="context-bar-fill" style={{ width: `${percent}%` }} />
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
