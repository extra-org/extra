import type { AgentChatConfig, AgentChatConfigInput, AgentChatMode, AgentChatPosition } from "../types";

export const DEFAULT_CONFIG: Omit<AgentChatConfig, "endpoint"> = {
  title: "Assistant",
  color: "#18181b",
  greeting: "",
  position: "bottom-right",
  avatar: "",
  mode: "floating",
  user: "",
};

const HEX_COLOR = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

export function normalizeEndpoint(value: string): string {
  return value.replace(/\/+$/, "");
}

export function safeColor(value: string | null | undefined): string {
  return value && HEX_COLOR.test(value) ? value : DEFAULT_CONFIG.color;
}

export function safePosition(value: string | null | undefined): AgentChatPosition {
  return value === "bottom-left" || value === "bottom-right" ? value : DEFAULT_CONFIG.position;
}

export function safeMode(value: string | null | undefined): AgentChatMode {
  return value === "inline" || value === "floating" ? value : DEFAULT_CONFIG.mode;
}

export function parseConfig(element: HTMLElement, scriptOrigin: string): AgentChatConfig {
  const endpoint = element.getAttribute("endpoint") || scriptOrigin;
  return {
    endpoint: normalizeEndpoint(endpoint),
    title: element.getAttribute("title") || DEFAULT_CONFIG.title,
    color: safeColor(element.getAttribute("color")),
    greeting: element.getAttribute("greeting") || DEFAULT_CONFIG.greeting,
    position: safePosition(element.getAttribute("position")),
    avatar: element.getAttribute("avatar") || DEFAULT_CONFIG.avatar,
    mode: safeMode(element.getAttribute("mode")),
    user: element.getAttribute("user") || DEFAULT_CONFIG.user,
  };
}

export function applyConfigAttributes(element: HTMLElement, config: AgentChatConfigInput): void {
  for (const [key, value] of Object.entries(config)) {
    if (value !== undefined && value !== null) {
      element.setAttribute(key, String(value));
    }
  }
}
