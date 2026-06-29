// src/agent_manager/api/static/widget/config/parseConfig.ts
var DEFAULT_CONFIG = {
  title: "Assistant",
  color: "#2563eb",
  greeting: "",
  position: "bottom-right",
  avatar: "",
  mode: "floating"
};
var HEX_COLOR = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;
function normalizeEndpoint(value) {
  return value.replace(/\/+$/, "");
}
function safeColor(value) {
  return value && HEX_COLOR.test(value) ? value : DEFAULT_CONFIG.color;
}
function safePosition(value) {
  return value === "bottom-left" || value === "bottom-right" ? value : DEFAULT_CONFIG.position;
}
function safeMode(value) {
  return value === "inline" || value === "floating" ? value : DEFAULT_CONFIG.mode;
}
function parseConfig(element, scriptOrigin) {
  const endpoint = element.getAttribute("endpoint") || scriptOrigin;
  return {
    endpoint: normalizeEndpoint(endpoint),
    title: element.getAttribute("title") || DEFAULT_CONFIG.title,
    color: safeColor(element.getAttribute("color")),
    greeting: element.getAttribute("greeting") || DEFAULT_CONFIG.greeting,
    position: safePosition(element.getAttribute("position")),
    avatar: element.getAttribute("avatar") || DEFAULT_CONFIG.avatar,
    mode: safeMode(element.getAttribute("mode"))
  };
}
function applyConfigAttributes(element, config) {
  for (const [key, value] of Object.entries(config)) {
    if (value !== void 0 && value !== null) {
      element.setAttribute(key, String(value));
    }
  }
}

// src/agent_manager/api/static/widget/api/AgentChatClient.ts
var AgentChatClient = class {
  constructor(endpoint) {
    this.endpoint = endpoint;
  }
  async createConversation() {
    const response = await fetch(`${this.endpoint}/conversations`, { method: "POST" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    return String(data.conversation_id);
  }
  async getMessages(conversationId) {
    const response = await fetch(`${this.endpoint}/conversations/${conversationId}/messages`);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return await response.json();
  }
  async sendMessage(conversationId, message) {
    const response = await fetch(`${this.endpoint}/conversations/${conversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message })
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    return {
      answer: String(data.answer || ""),
      visited: Array.isArray(data.visited) ? data.visited : void 0,
      used_tools: Array.isArray(data.used_tools) ? data.used_tools : void 0
    };
  }
};

// src/agent_manager/api/static/widget/security/renderMessage.ts
function escapeHtml(value) {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function formatAssistantText(value) {
  let out = escapeHtml(value);
  out = out.replace(/```([\s\S]*?)```/g, (_, code) => {
    return `<pre><code>${code.trim()}</code></pre>`;
  });
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  return out;
}

// src/agent_manager/api/static/widget/storage/conversationStorage.ts
function conversationStorageKey(endpoint) {
  return `agent-chat:${endpoint}`;
}
function getStoredConversationId(endpoint, storage = localStorage) {
  return storage.getItem(conversationStorageKey(endpoint));
}
function setStoredConversationId(endpoint, conversationId, storage = localStorage) {
  storage.setItem(conversationStorageKey(endpoint), conversationId);
}

// src/agent_manager/api/static/widget/styles/styles.ts
function styles(config) {
  const side = config.position === "bottom-left" ? "left" : "right";
  return `
    :host { all: initial; }
    * { box-sizing: border-box; font-family: -apple-system, system-ui, sans-serif; }
    .launcher {
      position: fixed; bottom: 20px; ${side}: 20px; width: 56px; height: 56px;
      border: 0; border-radius: 50%; background: ${config.color}; color: #fff; cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 10px 28px rgba(0,0,0,.2), 0 0 0 1px rgba(0,0,0,.04);
      z-index: 2147483000; transition: transform .15s; }
    .launcher:hover { transform: scale(1.06); }
    .launcher svg { width: 26px; height: 26px; }
    .panel {
      position: fixed; bottom: 88px; ${side}: 20px; width: 380px; height: 560px;
      max-height: calc(100vh - 120px); background: #fff; border-radius: 20px; overflow: hidden;
      border: 1px solid #e4e4e7;
      display: flex; flex-direction: column;
      box-shadow: 0 20px 60px rgba(0,0,0,.14), 0 2px 8px rgba(0,0,0,.06);
      opacity: 0; transform: translateY(12px) scale(.98); pointer-events: none;
      transition: opacity .18s ease, transform .18s ease; z-index: 2147483000; }
    .panel.open { opacity: 1; transform: none; pointer-events: auto; }
    .panel.inline { position: static; opacity: 1; transform: none; pointer-events: auto;
      box-shadow: 0 4px 18px rgba(0,0,0,.12); }
    .header { background: #fff; color: #18181b; padding: 14px 18px; font-weight: 600;
      font-size: 14px; display: flex; align-items: center; gap: 10px;
      border-bottom: 1px solid #f0f0f1; }
    .header .dot { width: 22px; height: 22px; border-radius: 50%; flex: 0 0 auto;
      background: ${config.color}; background-size: cover; background-position: center; }
    .header .title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .close { background: transparent; border: 0; color: #71717a; cursor: pointer;
      padding: 0; width: 30px; height: 30px; border-radius: 50%;
      display: flex; align-items: center; justify-content: center; transition: background .12s; }
    .close:hover { background: #f4f4f5; color: #18181b; }
    .close svg { width: 16px; height: 16px; }
    .body { flex: 1; min-height: 0; display: flex; flex-direction: column; background: #fff; }
    .messages { flex: 1; min-height: 0; overflow-y: auto; padding: 16px 18px;
      display: flex; flex-direction: column; gap: 14px; }
    .msg { font-size: 14.5px; line-height: 1.55; word-wrap: break-word; white-space: pre-wrap; }
    .msg.ai { color: #18181b; max-width: 100%; }
    .msg.ai.typing { color: #a1a1aa; letter-spacing: 1px; }
    .msg.user { background: #f4f4f5; color: #18181b; border-radius: 18px;
      padding: 10px 14px; margin-left: auto; max-width: 88%; }
    .msg code { background: #f4f4f5; border-radius: 4px; padding: 1px 5px; font-size: 13px; }
    .msg pre { background: #f4f4f5; border-radius: 10px; padding: 10px 12px; overflow-x: auto; margin: 0; }
    .msg pre code { background: none; padding: 0; white-space: pre-wrap; }
    .composer { display: flex; align-items: flex-end; gap: 8px; padding: 12px 14px;
      border-top: 1px solid #f0f0f1; }
    .input { flex: 1; resize: none; max-height: 120px; border: 1px solid #e4e4e7;
      border-radius: 20px; padding: 10px 16px; font-size: 14.5px; color: #18181b;
      font-family: inherit; background: #fff; outline: none; }
    .input:focus { border-color: ${config.color}; }
    .input::placeholder { color: #a1a1aa; }
    .send { flex: 0 0 auto; width: 34px; height: 34px; border-radius: 50%; border: 0;
      background: ${config.color}; color: #fff; cursor: pointer;
      display: flex; align-items: center; justify-content: center; transition: opacity .12s; }
    .send:hover { opacity: .88; }
    .send:disabled { opacity: .4; cursor: default; }
    .send svg { width: 16px; height: 16px; }
    @media (prefers-reduced-motion: reduce) {
      .launcher,
      .close,
      .send,
      .panel {
        transition: none;
      }
      .launcher:hover { transform: none; }
    }
    @media (max-width: 480px) {
      .panel:not(.inline) { width: 100vw; height: 100dvh; max-height: 100dvh;
        bottom: 0; ${side}: 0; border-radius: 0; }
    }`;
}

// src/agent_manager/api/static/widget/element/AgentChatElement.ts
var CHAT_ICON = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 3C6.5 3 2 6.8 2 11.5c0 2.3 1.1 4.4 2.9 5.9L4 21l4.3-1.5c1.1.3 2.4.5 3.7.5 5.5 0 10-3.8 10-8.5S17.5 3 12 3z"/></svg>';
var CLOSE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
var SEND_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
var nextWidgetId = 0;
var AgentChatElement = class extends HTMLElement {
  constructor(scriptOrigin = defaultScriptOrigin()) {
    super();
    this.scriptOrigin = scriptOrigin;
    this.storeKey = "";
    this.cleanupCallbacks = [];
    this.connected = false;
    this.loaded = false;
    this.open = false;
    this.entries = [];
    this.widgetId = `agent-chat-${++nextWidgetId}`;
    this.panelId = `${this.widgetId}-panel`;
    this.titleId = `${this.widgetId}-title`;
    this.launcher = null;
    this.panel = null;
    this.messages = null;
    this.input = null;
    this.sendButton = null;
  }
  static get observedAttributes() {
    return ["endpoint", "title", "color", "greeting", "position", "avatar", "mode"];
  }
  connectedCallback() {
    if (this.connected) return;
    this.connected = true;
    this.configure();
    this.render();
    if (this.config.mode === "inline") {
      void this.loadHistory();
    }
  }
  disconnectedCallback() {
    this.cleanup();
    this.connected = false;
  }
  attributeChangedCallback(name, oldValue, newValue) {
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
  configure() {
    this.config = parseConfig(this, this.scriptOrigin);
    this.client = new AgentChatClient(this.config.endpoint);
    this.storeKey = conversationStorageKey(this.config.endpoint);
  }
  render() {
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
        const keyboard = event;
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
    input.placeholder = "Message\u2026";
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
          const keyboard = event;
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
      const keyboard = event;
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
  listen(target, type, handler) {
    target.addEventListener(type, handler);
    this.cleanupCallbacks.push(() => target.removeEventListener(type, handler));
  }
  cleanup() {
    for (const callback of this.cleanupCallbacks.splice(0)) callback();
  }
  async toggle() {
    if (this.open) {
      this.closeChat({ restoreFocus: true });
      return;
    }
    await this.openChat();
  }
  async openChat() {
    if (this.config.mode === "inline") return;
    this.open = true;
    this.panel?.classList.add("open");
    this.launcher?.setAttribute("aria-expanded", "true");
    if (!this.loaded) await this.loadHistory();
    this.focusComposer();
  }
  closeChat({ restoreFocus }) {
    if (this.config.mode === "inline") return;
    this.open = false;
    this.panel?.classList.remove("open");
    this.launcher?.setAttribute("aria-expanded", "false");
    if (restoreFocus) this.launcher?.focus?.({ preventScroll: true });
  }
  focusComposer() {
    this.input?.focus?.({ preventScroll: true });
  }
  async conversationId() {
    let id = getStoredConversationId(this.config.endpoint);
    if (!id) {
      id = await this.client.createConversation();
      setStoredConversationId(this.config.endpoint, id);
    }
    return id;
  }
  async loadHistory() {
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
    }
  }
  addMessage(role, text) {
    const entry = { role, text };
    this.entries.push(entry);
    return this.renderMessage(entry);
  }
  renderMessage(entry) {
    const element = document.createElement("div");
    element.className = `msg ${entry.role === "user" ? "user" : "ai"}`;
    if (entry.typing) {
      element.classList.add("typing");
      element.setAttribute("role", "status");
      element.setAttribute("aria-label", "Assistant is typing");
      element.textContent = "\xB7\xB7\xB7";
    } else {
      element.innerHTML = entry.role === "user" ? escapeHtml(entry.text) : formatAssistantText(entry.text);
    }
    this.messages?.appendChild(element);
    if (this.messages) this.messages.scrollTop = this.messages.scrollHeight;
    return element;
  }
  setTyping(on) {
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
  async submit() {
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
  emitAnswer(data) {
    try {
      const detail = {
        visited: data.visited ?? [],
        used_tools: data.used_tools ?? []
      };
      if (detail.visited.length) {
        console.debug?.("[agent-chat] route:", detail.visited.join(" \u2192 "), detail.used_tools);
      }
      if (typeof CustomEvent === "function" && typeof this.dispatchEvent === "function") {
        this.dispatchEvent(
          new CustomEvent("agent-chat:answer", {
            detail,
            bubbles: true,
            composed: true
          })
        );
      }
    } catch {
    }
  }
};
function defaultScriptOrigin() {
  return new URL(import.meta.url).origin;
}

// src/agent_manager/api/static/widget/element/defineAgentChat.ts
function defineAgentChat(scriptOrigin) {
  if (customElements.get("agent-chat")) return;
  customElements.define(
    "agent-chat",
    class extends AgentChatElement {
      constructor() {
        super(scriptOrigin);
      }
    }
  );
}
function autoMountAgentChat(config = window.agentChatConfig || {}) {
  if (document.querySelector("agent-chat")) return;
  const element = document.createElement("agent-chat");
  applyConfigAttributes(element, config);
  document.body.appendChild(element);
}

// src/agent_manager/api/static/widget/index.ts
if (typeof document !== "undefined") {
  const scriptOrigin = new URL(import.meta.url).origin;
  defineAgentChat(scriptOrigin);
  const mount = () => autoMountAgentChat();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount, { once: true });
  } else {
    setTimeout(mount, 0);
  }
}
export {
  AgentChatClient,
  AgentChatElement,
  DEFAULT_CONFIG,
  applyConfigAttributes,
  autoMountAgentChat,
  conversationStorageKey,
  defineAgentChat,
  escapeHtml,
  formatAssistantText,
  getStoredConversationId,
  parseConfig,
  setStoredConversationId
};
