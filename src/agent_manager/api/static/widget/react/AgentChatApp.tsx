import { useCallback, useEffect, useRef, useState } from "react";

import type { AgentChatClient } from "../api/AgentChatClient";
import {
  conversationStorageKey,
  getStoredConversationId,
  setStoredConversationId,
} from "../storage/conversationStorage";
import type { AgentChatAnswerDetail, AgentChatConfig, ToolRecord } from "../types";
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

type MessageEntry = {
  role: "user" | "ai";
  text: string;
  typing?: boolean;
  route?: string[];
  tools?: ToolRecord[];
};

export interface AgentChatAppProps {
  client: AgentChatClient;
  config: AgentChatConfig;
  onAnswer: (detail: AgentChatAnswerDetail) => void;
  panelId: string;
  titleId: string;
}

export function AgentChatApp({ client, config, onAnswer, panelId, titleId }: AgentChatAppProps) {
  const inline = config.mode === "inline";
  const [open, setOpen] = useState(inline);
  const [loaded, setLoaded] = useState(false);
  const [sending, setSending] = useState(false);
  const [entries, setEntries] = useState<MessageEntry[]>([]);
  const launcherRef = useRef<HTMLButtonElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  const loadHistory = useCallback(async () => {
    if (loaded) return;
    setLoaded(true);
    const existing = localStorage.getItem(conversationStorageKey(config.endpoint));
    if (!existing) {
      if (config.greeting) setEntries((prev) => [...prev, { role: "ai", text: config.greeting }]);
      return;
    }
    try {
      const history = await client.getMessages(existing);
      setEntries(
        history.map((message) => ({
          role: message.role === "user" ? "user" : "ai",
          text: message.content,
        })),
      );
    } catch {
      // Offline on load is non-fatal.
    }
  }, [client, config.endpoint, config.greeting, loaded]);

  useEffect(() => {
    if (inline) {
      void loadHistory();
    }
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

  const conversationId = useCallback(async () => {
    let id = getStoredConversationId(config.endpoint);
    if (!id) {
      id = await client.createConversation();
      setStoredConversationId(config.endpoint, id);
    }
    return id;
  }, [client, config.endpoint]);

  const submit = useCallback(
    async (text: string) => {
      setEntries((prev) => [...prev, { role: "user", text }, { role: "ai", text: "", typing: true }]);
      setSending(true);
      try {
        const id = await conversationId();
        const data = await client.sendMessage(id, text);
        setEntries((prev) => [
          ...prev.filter((entry) => !entry.typing),
          { role: "ai", text: data.answer, route: data.visited, tools: data.used_tools },
        ]);
        onAnswer({ visited: data.visited ?? [], used_tools: data.used_tools ?? [] });
      } catch {
        setEntries((prev) => [
          ...prev.filter((entry) => !entry.typing),
          { role: "ai", text: "Something went wrong. Please try again." },
        ]);
      } finally {
        setSending(false);
      }
    },
    [client, conversationId, onAnswer],
  );

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
        <button
          aria-controls={panelId}
          aria-expanded={open}
          aria-label="Open chat"
          className="launcher"
          onClick={() => void (open ? closeChat() : openChat())}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              void openChat();
            }
          }}
          ref={launcherRef}
          type="button"
        >
          <ChatIcon />
        </button>
      ) : null}

      <section
        aria-labelledby={titleId}
        className={`panel${inline ? " inline" : ""}${open && !inline ? " open" : ""}`}
        id={panelId}
        role={inline ? "region" : "dialog"}
      >
        <header className="header">
          <span
            className="dot"
            style={config.avatar ? { backgroundImage: `url("${config.avatar.replace(/"/g, "%22")}")` } : undefined}
          />
          <span className="title" id={titleId}>
            {config.title}
          </span>
          {!inline ? (
            <button aria-label="Close chat" className="close" onClick={closeChat} type="button">
              <CloseIcon />
            </button>
          ) : null}
        </header>

        <div className="body">
          <Conversation>
            <ConversationContent>
              {entries.map((entry, index) => (
                <Message
                  key={`${entry.role}-${index}-${entry.text}`}
                  from={entry.role === "user" ? "user" : "assistant"}
                  typing={entry.typing}
                >
                  {entry.typing ? (
                    "..."
                  ) : (
                    <>
                      {entry.role === "ai" ? <ToolMessage route={entry.route} tools={entry.tools} /> : null}
                      <MessageContent>
                        {entry.role === "ai" ? <MessageResponse>{entry.text}</MessageResponse> : entry.text}
                      </MessageContent>
                    </>
                  )}
                </Message>
              ))}
            </ConversationContent>
          </Conversation>
          <PromptInput onSubmit={(message) => void submit(message.text)}>
            <PromptInputTextarea
              aria-label="Message"
              disabled={false}
              inputRef={inputRef}
              onSubmit={() => {
                inputRef.current?.form?.requestSubmit();
              }}
              placeholder="Message..."
            />
            <PromptInputFooter>
              <span className="prompt-hint">Enter to send · Shift+Enter for a new line</span>
              <PromptInputSubmit disabled={sending} />
            </PromptInputFooter>
          </PromptInput>
        </div>
      </section>
    </div>
  );
}

function ToolMessage({ route, tools = [] }: { route?: string[]; tools?: ToolRecord[] }) {
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
          <ToolHeader state={toolState(tool.status)} title={tool.provider ? `${tool.name} · ${tool.provider}` : tool.name} />
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

function toolState(status: string): ToolState {
  if (status === "failed") return "output-error";
  if (status === "succeeded") return "output-available";
  return "input-available";
}

function ChatIcon() {
  return (
    <svg aria-hidden="true" fill="currentColor" viewBox="0 0 24 24">
      <path d="M12 3C6.5 3 2 6.8 2 11.5c0 2.3 1.1 4.4 2.9 5.9L4 21l4.3-1.5c1.1.3 2.4.5 3.7.5 5.5 0 10-3.8 10-8.5S17.5 3 12 3z" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeWidth="2.2"
      viewBox="0 0 24 24"
    >
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}
