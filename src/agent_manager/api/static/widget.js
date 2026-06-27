// agent-chat: a drop-in, brandable chat widget for any web app.
//
// A floating launcher (or inline panel), wired to an agent_manager backend.
// Add two lines to your own app:
//
//   <script type="module" src="https://your-backend/widget.js"></script>
//   <agent-chat title="Support" color="#7c3aed"></agent-chat>
//
// Attributes (all optional):
//   endpoint  backend base URL          (default: the script's origin)
//   title     header text               (default: "Assistant")
//   color     accent color              (default: "#2563eb")
//   greeting  first assistant message   (default: none)
//   position  bottom-right | bottom-left(default: bottom-right)
//   avatar    assistant avatar image URL
//   mode      floating | inline         (default: floating)
//
// If a host page/app can only inject a <script> tag (e.g. a CMS snippet, or
// Home Assistant's `frontend.extra_module_url`) and has no way to also place
// an <agent-chat> tag, this script auto-mounts one onto <body> using
// `window.agentChatConfig` (same keys as the attributes above) for branding.
//
// Shadow-DOM isolated, so it never inherits or leaks the host app's styles.
// No third-party scripts or CDNs — everything needed to render the chat UI
// lives in this one file, so it can't break under a host page's CSP or a
// dependency's breaking release.

export function escapeHtml(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ponytail: only bold/inline-code/code-fences are recognized — no italics,
// lists, or links. Add them here if agent answers actually use them.
// Line breaks are handled by CSS `white-space: pre-wrap` on `.msg`, not here.
export function formatAssistantText(s) {
  let out = escapeHtml(s);
  out = out.replace(/```([\s\S]*?)```/g, (_, code) => `<pre><code>${code.trim()}</code></pre>`);
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  return out;
}

(function () {
  // Lets this module be `import`-ed under Node (see widget.test.mjs)
  // without touching any browser-only API.
  if (typeof document === "undefined") return;

  // Loaded as a module (<script type="module"> or a dynamic import(), as
  // Home Assistant's extra_module_url does) so `document.currentScript` is
  // always null — import.meta.url is the only reliable way to find our own
  // origin in every loading mode.
  const SELF_ORIGIN = new URL(import.meta.url).origin;

  const CHAT_ICON =
    '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 3C6.5 3 2 6.8 2 11.5c0 2.3 1.1 4.4 2.9 5.9L4 21l4.3-1.5c1.1.3 2.4.5 3.7.5 5.5 0 10-3.8 10-8.5S17.5 3 12 3z"/></svg>';
  const CLOSE_ICON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  const SEND_ICON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';

  function styles(cfg) {
    const side = cfg.position === "bottom-left" ? "left" : "right";
    return `
      :host { all: initial; }
      * { box-sizing: border-box; font-family: -apple-system, system-ui, sans-serif; }
      .launcher {
        position: fixed; bottom: 20px; ${side}: 20px; width: 56px; height: 56px;
        border: 0; border-radius: 50%; background: ${cfg.color}; color: #fff; cursor: pointer;
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
        background: ${cfg.color}; background-size: cover; background-position: center; }
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
      .input:focus { border-color: ${cfg.color}; }
      .input::placeholder { color: #a1a1aa; }
      .send { flex: 0 0 auto; width: 34px; height: 34px; border-radius: 50%; border: 0;
        background: ${cfg.color}; color: #fff; cursor: pointer;
        display: flex; align-items: center; justify-content: center; transition: opacity .12s; }
      .send:hover { opacity: .88; }
      .send:disabled { opacity: .4; cursor: default; }
      .send svg { width: 16px; height: 16px; }
      @media (max-width: 480px) {
        .panel:not(.inline) { width: 100vw; height: 100dvh; max-height: 100dvh;
          bottom: 0; ${side}: 0; border-radius: 0; }
      }`;
  }

  class AgentChat extends HTMLElement {
    async connectedCallback() {
      const cfg = {
        endpoint: (this.getAttribute("endpoint") || SELF_ORIGIN).replace(/\/$/, ""),
        title: this.getAttribute("title") || "Assistant",
        color: this.getAttribute("color") || "#2563eb",
        greeting: this.getAttribute("greeting") || "",
        position: this.getAttribute("position") || "bottom-right",
        avatar: this.getAttribute("avatar") || "",
        mode: this.getAttribute("mode") || "floating",
      };
      this._cfg = cfg;
      this._storeKey = "agent-chat:" + cfg.endpoint;

      const inline = cfg.mode === "inline";
      const root = this.attachShadow({ mode: "open" });
      root.innerHTML = `
        <style>${styles(cfg)}</style>
        ${inline ? "" : `<button class="launcher" aria-label="Open chat">${CHAT_ICON}</button>`}
        <div class="panel${inline ? " inline" : ""}">
          <div class="header">
            <span class="dot"${cfg.avatar ? ` style="background-image:url('${cfg.avatar}')"` : ""}></span>
            <span class="title">${cfg.title}</span>
            ${inline ? "" : `<button class="close" aria-label="Close chat">${CLOSE_ICON}</button>`}
          </div>
          <div class="body">
            <div class="messages"></div>
            <div class="composer">
              <textarea class="input" placeholder="Message…" rows="1"></textarea>
              <button class="send" aria-label="Send">${SEND_ICON}</button>
            </div>
          </div>
        </div>`;

      this._panel = root.querySelector(".panel");
      this._messages = root.querySelector(".messages");
      this._input = root.querySelector(".input");
      this._sendBtn = root.querySelector(".send");

      // Host pages often bind global keyboard shortcuts on `document` (Home
      // Assistant does). Shadow DOM hides our focused input from their
      // "am I typing in a field?" checks, so without this, typing in the
      // chat triggers the host app's shortcuts. Stop key events at our own
      // boundary so they never reach the host page.
      for (const type of ["keydown", "keyup", "keypress"]) {
        root.addEventListener(type, (e) => e.stopPropagation());
      }

      this._input.addEventListener("input", () => {
        this._input.style.height = "auto";
        this._input.style.height = Math.min(this._input.scrollHeight, 120) + "px";
      });
      this._input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          this._submit();
        }
      });
      this._sendBtn.addEventListener("click", () => this._submit());

      if (inline) {
        this._loadHistory();
      } else {
        root.querySelector(".launcher").addEventListener("click", () => this._toggle());
        root.querySelector(".close").addEventListener("click", () => this._panel.classList.remove("open"));
      }
    }

    async _toggle() {
      const opening = !this._panel.classList.contains("open");
      this._panel.classList.toggle("open", opening);
      if (opening && !this._loaded) await this._loadHistory();
    }

    async _conversationId() {
      let id = localStorage.getItem(this._storeKey);
      if (!id) {
        const r = await fetch(this._cfg.endpoint + "/conversations", { method: "POST" });
        id = (await r.json()).conversation_id;
        localStorage.setItem(this._storeKey, id);
      }
      return id;
    }

    async _loadHistory() {
      this._loaded = true;
      const existing = localStorage.getItem(this._storeKey);
      if (!existing) {
        if (this._cfg.greeting) this._addMessage("ai", this._cfg.greeting);
        return;
      }
      try {
        const r = await fetch(`${this._cfg.endpoint}/conversations/${existing}/messages`);
        if (!r.ok) return;
        const history = await r.json();
        for (const m of history) this._addMessage(m.role === "user" ? "user" : "ai", m.content);
      } catch {
        /* offline on load is non-fatal */
      }
    }

    _addMessage(role, text) {
      const el = document.createElement("div");
      el.className = "msg " + (role === "user" ? "user" : "ai");
      el.innerHTML = role === "user" ? escapeHtml(text) : formatAssistantText(text);
      this._messages.appendChild(el);
      this._messages.scrollTop = this._messages.scrollHeight;
      return el;
    }

    _setTyping(on) {
      if (on) {
        this._typingEl = this._addMessage("ai", "");
        this._typingEl.classList.add("typing");
        this._typingEl.textContent = "···";
      } else if (this._typingEl) {
        this._typingEl.remove();
        this._typingEl = null;
      }
    }

    async _submit() {
      const text = this._input.value.trim();
      if (!text) return;
      this._input.value = "";
      this._input.style.height = "auto";
      this._addMessage("user", text);
      this._sendBtn.disabled = true;
      this._setTyping(true);
      try {
        const id = await this._conversationId();
        const r = await fetch(`${this._cfg.endpoint}/conversations/${id}/messages`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text }),
        });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const data = await r.json();
        this._setTyping(false);
        this._addMessage("ai", data.answer);
      } catch {
        this._setTyping(false);
        this._addMessage("ai", "Something went wrong. Please try again.");
      } finally {
        this._sendBtn.disabled = false;
      }
    }
  }

  customElements.define("agent-chat", AgentChat);

  function autoMount() {
    if (document.querySelector("agent-chat")) return;
    const el = document.createElement("agent-chat");
    const cfg = (typeof window !== "undefined" && window.agentChatConfig) || {};
    for (const [k, v] of Object.entries(cfg)) el.setAttribute(k, String(v));
    document.body.appendChild(el);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoMount);
  } else {
    setTimeout(autoMount, 0);
  }
})();
