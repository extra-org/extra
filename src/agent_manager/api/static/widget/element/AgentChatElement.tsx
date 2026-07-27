import { createRoot, type Root } from "react-dom/client";

import { AgentChatClient } from "../api/AgentChatClient";
import { parseConfig } from "../config/parseConfig";
import { AgentChatApp } from "../react/AgentChatApp";
import { styles } from "../styles/styles";
import type { AgentChatAnswerDetail, AgentChatConfig } from "../types";

let nextWidgetId = 0;

export class AgentChatElement extends HTMLElement {
  static get observedAttributes(): string[] {
    return ["endpoint", "title", "color", "greeting", "position", "avatar", "mode", "user"];
  }

  private config!: AgentChatConfig;
  private client!: AgentChatClient;
  private connected = false;
  private reactRoot: Root | null = null;
  private instanceKey = 0;
  private readonly widgetId = `agent-chat-${++nextWidgetId}`;
  private readonly panelId = `${this.widgetId}-panel`;
  private readonly titleId = `${this.widgetId}-title`;

  constructor(private readonly scriptOrigin: string = defaultScriptOrigin()) {
    super();
  }

  connectedCallback(): void {
    if (this.connected) return;
    this.connected = true;
    this.configure();
    this.render();
  }

  disconnectedCallback(): void {
    this.reactRoot?.unmount();
    this.reactRoot = null;
    this.connected = false;
  }

  attributeChangedCallback(_name: string, oldValue: string | null, newValue: string | null): void {
    if (!this.connected || oldValue === newValue) return;
    const previousEndpoint = this.config?.endpoint;
    this.configure();
    if (previousEndpoint && previousEndpoint !== this.config.endpoint) this.instanceKey += 1;
    this.render();
  }

  private configure(): void {
    this.config = parseConfig(this, this.scriptOrigin);
    this.client = new AgentChatClient(this.config.endpoint);
  }

  private render(): void {
    const root = this.shadowRoot || this.attachShadow({ mode: "open" });
    this.reactRoot?.unmount();
    this.reactRoot = null;
    root.replaceChildren();

    const style = document.createElement("style");
    style.textContent = styles(this.config);
    root.appendChild(style);

    const mount = document.createElement("div");
    mount.className = "react-mount";
    root.appendChild(mount);
    this.reactRoot = createRoot(mount);
    this.reactRoot.render(
      <AgentChatApp
        key={this.instanceKey}
        client={this.client}
        config={this.config}
        onAnswer={(detail) => this.emitAnswer(detail)}
        panelId={this.panelId}
        titleId={this.titleId}
      />,
    );
  }

  /**
   * Surface safe routing metadata (the agent graph path and any tools used) so
   * host pages can observe which agent/sub-agent handled a turn. This does not
   * expose reasoning or hidden content.
   */
  private emitAnswer(detail: AgentChatAnswerDetail): void {
    try {
      if (detail.visited.length) {
        console.debug?.("[agent-chat] route:", detail.visited.join(" -> "), detail.used_tools);
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
