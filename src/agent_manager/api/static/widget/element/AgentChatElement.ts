import { AgentChatClient } from "../api/AgentChatClient";
import { parseConfig } from "../config/parseConfig";
import { escapeHtml, formatAssistantText } from "../security/renderMessage";
import {
  conversationStorageKey,
  getStoredConversationId,
  setStoredConversationId,
} from "../storage/conversationStorage";
import { styles } from "../styles/styles";
import type { AgentChatAnswerDetail, AgentChatConfig, SendMessageResponse } from "../types";

const CHAT_ICON =
  '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 3C6.5 3 2 6.8 2 11.5c0 2.3 1.1 4.4 2.9 5.9L4 21l4.3-1.5c1.1.3 2.4.5 3.7.5 5.5 0 10-3.8 10-8.5S17.5 3 12 3z"/></svg>';
const CLOSE_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
const SEND_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';

type MessageEntry = { role: "user" | "ai"; text: string; typing?: boolean };

let nextWidgetId = 0;

export class AgentChatElement extends HTMLElement {
  static get observedAttributes(): string[] {
    return ["endpoint", "title", "color", "greeting", "position", "avatar", "mode"];
  }

  private config!: AgentChatConfig;
  private client!: AgentChatClient;
  private storeKey = "";
  private cleanupCallbacks: Array<() => void> = [];
  private connected = false;
  private loaded = false;
  private open = false;
  private entries: MessageEntry[] = [];
  private readonly widgetId = `agent-chat-${++nextWidgetId}`;
  private readonly panelId = `${this.widgetId}-panel`;
  private readonly titleId = `${this.widgetId}-title`;
  private launcher: HTMLButtonElement | null = null;
  private panel: HTMLElement | null = null;
  private messages: HTMLElement | null = null;
  private input: HTMLTextAreaElement | null = null;
  private sendButton: HTMLButtonElement | null = null;

  constructor(private readonly scriptOrigin: string = defaultScriptOrigin()) {
    super();
  }

  connectedCallback(): void {
    if (this.connected) return;
    this.connected = true;
    this.configure();
    this.render();
    if (this.config.mode === "inline") {
      void this.loadHistory();
    }
  }

  disconnectedCallback(): void {
    this.cleanup();
    this.connected = false;
  }

  attributeChangedCallback(name: string, oldValue: string | null, newValue: string | null): void {
    if (!this.connected || oldValue === newValue) return;
    const previousEndpoint = this.config?.endpoint;
    this.configure();
    if (previousEndpoint && previousEndpoint !== this.config.endpoint) {
      this.entries = [];
      this.loaded = false;
    }
    this.render();
    if (this.config.mode === "inline" && !this.loaded) void this.loadHistory();
  }

  private configure(): void {
    this.config = parseConfig(this, this.scriptOrigin);
    this.client = new AgentChatClient(this.config.endpoint);
    this.storeKey = conversationStorageKey(this.config.endpoint);
  }

  private render(): void {
    this.cleanup();
    const root = this.shadowRoot || this.attachShadow({ mode: "open" });
    root.replaceChildren();

    const style = document.createElement("style");
    style.textContent = styles(this.config);
    root.appendChild(style);

    const inline = this.config.mode === "inline";
    if (!inline) {
      const launcher = document.createElement("button");
      launcher.className = "launcher";
      launcher.setAttribute("aria-label", "Open chat");
      launcher.setAttribute("aria-controls", this.panelId);
      launcher.setAttribute("aria-expanded", String(this.open));
      launcher.innerHTML = CHAT_ICON;
      root.appendChild(launcher);
      this.launcher = launcher;
      this.listen(launcher, "click", () => void this.toggle());
      this.listen(launcher, "keydown", (event) => {
        const keyboard = event as KeyboardEvent;
        if (keyboard.key === "Enter" || keyboard.key === " ") {
          keyboard.preventDefault();
          void this.openChat();
        }
      });
    } else {
      this.launcher = null;
    }

    const panel = document.createElement("div");
    panel.id = this.panelId;
    panel.className = `panel${inline ? " inline" : ""}${this.open && !inline ? " open" : ""}`;
    panel.setAttribute("aria-labelledby", this.titleId);
    if (inline) {
      panel.setAttribute("role", "region");
    } else {
      panel.setAttribute("role", "dialog");
    }
    root.appendChild(panel);
    this.panel = panel;

    const header = document.createElement("div");
    header.className = "header";
    panel.appendChild(header);

    const dot = document.createElement("span");
    dot.className = "dot";
    if (this.config.avatar) {
      dot.style.backgroundImage = `url("${this.config.avatar.replace(/"/g, "%22")}")`;
    }
    header.appendChild(dot);

    const title = document.createElement("span");
    title.id = this.titleId;
    title.className = "title";
    title.textContent = this.config.title;
    header.appendChild(title);

    if (!inline) {
      const close = document.createElement("button");
      close.className = "close";
      close.setAttribute("aria-label", "Close chat");
      close.innerHTML = CLOSE_ICON;
      header.appendChild(close);
      this.listen(close, "click", () => this.closeChat({ restoreFocus: true }));
    }

    const body = document.createElement("div");
    body.className = "body";
    panel.appendChild(body);

    const messages = document.createElement("div");
    messages.className = "messages";
    messages.setAttribute("aria-live", "polite");
    messages.setAttribute("aria-relevant", "additions text");
    body.appendChild(messages);
    this.messages = messages;

    const composer = document.createElement("div");
    composer.className = "composer";
    body.appendChild(composer);

    const input = document.createElement("textarea");
    input.className = "input";
    input.setAttribute("aria-label", "Message");
    input.placeholder = "Message…";
    input.rows = 1;
    composer.appendChild(input);
    this.input = input;

    const send = document.createElement("button");
    send.className = "send";
    send.setAttribute("aria-label", "Send message");
    send.innerHTML = SEND_ICON;
    composer.appendChild(send);
    this.sendButton = send;

    for (const type of ["keydown", "keyup", "keypress"]) {
      this.listen(root, type, (event) => {
        if (type === "keydown") {
          const keyboard = event as KeyboardEvent;
          if (keyboard.key === "Escape" && !inline && this.open) {
            keyboard.preventDefault();
            this.closeChat({ restoreFocus: true });
          }
        }
        event.stopPropagation();
      });
    }
    this.listen(input, "input", () => {
      input.style.height = "auto";
      input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
    });
    this.listen(input, "keydown", (event) => {
      const keyboard = event as KeyboardEvent;
      if (keyboard.key === "Enter" && !keyboard.shiftKey) {
        keyboard.preventDefault();
        void this.submit();
      }
    });
    this.listen(send, "click", () => void this.submit());

    for (const entry of this.entries) {
      this.renderMessage(entry);
    }
  }

  private listen(target: EventTarget, type: string, handler: EventListener): void {
    target.addEventListener(type, handler);
    this.cleanupCallbacks.push(() => target.removeEventListener(type, handler));
  }

  private cleanup(): void {
    for (const callback of this.cleanupCallbacks.splice(0)) callback();
  }

  private async toggle(): Promise<void> {
    if (this.open) {
      this.closeChat({ restoreFocus: true });
      return;
    }
    await this.openChat();
  }

  private async openChat(): Promise<void> {
    if (this.config.mode === "inline") return;
    this.open = true;
    this.panel?.classList.add("open");
    this.launcher?.setAttribute("aria-expanded", "true");
    if (!this.loaded) await this.loadHistory();
    this.focusComposer();
  }

  private closeChat({ restoreFocus }: { restoreFocus: boolean }): void {
    if (this.config.mode === "inline") return;
    this.open = false;
    this.panel?.classList.remove("open");
    this.launcher?.setAttribute("aria-expanded", "false");
    if (restoreFocus) this.launcher?.focus?.({ preventScroll: true });
  }

  private focusComposer(): void {
    this.input?.focus?.({ preventScroll: true });
  }

  private async conversationId(): Promise<string> {
    let id = getStoredConversationId(this.config.endpoint);
    if (!id) {
      id = await this.client.createConversation();
      setStoredConversationId(this.config.endpoint, id);
    }
    return id;
  }

  private async loadHistory(): Promise<void> {
    this.loaded = true;
    const existing = localStorage.getItem(this.storeKey);
    if (!existing) {
      if (this.config.greeting) this.addMessage("ai", this.config.greeting);
      return;
    }
    try {
      const history = await this.client.getMessages(existing);
      for (const message of history) {
        this.addMessage(message.role === "user" ? "user" : "ai", message.content);
      }
    } catch {
      // Offline on load is non-fatal.
    }
  }

  private addMessage(role: "user" | "ai", text: string): HTMLElement {
    const entry: MessageEntry = { role, text };
    this.entries.push(entry);
    return this.renderMessage(entry);
  }

  private renderMessage(entry: MessageEntry): HTMLElement {
    const element = document.createElement("div");
    element.className = `msg ${entry.role === "user" ? "user" : "ai"}`;
    if (entry.typing) {
      element.classList.add("typing");
      element.setAttribute("role", "status");
      element.setAttribute("aria-label", "Assistant is typing");
      element.textContent = "···";
    } else {
      element.innerHTML = entry.role === "user" ? escapeHtml(entry.text) : formatAssistantText(entry.text);
    }
    this.messages?.appendChild(element);
    if (this.messages) this.messages.scrollTop = this.messages.scrollHeight;
    return element;
  }

  private setTyping(on: boolean): void {
    if (on) {
      this.entries.push({ role: "ai", text: "", typing: true });
      this.render();
      if (this.open) this.focusComposer();
      return;
    }
    const index = this.entries.findIndex((entry) => entry.typing);
    if (index >= 0) {
      this.entries.splice(index, 1);
      this.render();
      if (this.open) this.focusComposer();
    }
  }

  private async submit(): Promise<void> {
    if (!this.input || !this.sendButton) return;
    const text = this.input.value.trim();
    if (!text) return;
    this.input.value = "";
    this.input.style.height = "auto";
    this.addMessage("user", text);
    this.sendButton.disabled = true;
    this.setTyping(true);
    try {
      const id = await this.conversationId();
      const data = await this.client.sendMessage(id, text);
      this.setTyping(false);
      this.addMessage("ai", data.answer);
      this.emitAnswer(data);
    } catch {
      this.setTyping(false);
      this.addMessage("ai", "Something went wrong. Please try again.");
    } finally {
      if (this.sendButton) this.sendButton.disabled = false;
      this.focusComposer();
    }
  }

  /**
   * Surface safe routing metadata (the agent graph path and any tools used) so
   * host pages can observe which agent/sub-agent handled a turn. This does not
   * change the embed contract or the widget UI — it only emits a DOM event and
   * a console debug line. No reasoning or hidden content is exposed.
   */
  private emitAnswer(data: SendMessageResponse): void {
    try {
      const detail: AgentChatAnswerDetail = {
        visited: data.visited ?? [],
        used_tools: data.used_tools ?? [],
      };
      if (detail.visited.length) {
        console.debug?.("[agent-chat] route:", detail.visited.join(" → "), detail.used_tools);
      }
      if (typeof CustomEvent === "function" && typeof this.dispatchEvent === "function") {
        this.dispatchEvent(
          new CustomEvent<AgentChatAnswerDetail>("agent-chat:answer", {
            detail,
            bubbles: true,
            composed: true,
          }),
        );
      }
    } catch {
      // Observability must never break the chat flow.
    }
  }
}

function defaultScriptOrigin(): string {
  return new URL(import.meta.url).origin;
}
