import type { AgentChatConfig } from "../types";

export function styles(config: AgentChatConfig): string {
  const side = config.position === "bottom-left" ? "left" : "right";
  return `
    :host { all: initial; }
    * { box-sizing: border-box; font-family: -apple-system, system-ui, sans-serif; }
    .react-mount,
    .agent-chat-react { display: contents; }
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
    .messages { flex: 1; min-height: 0; overflow-y: auto; }
    .conversation-content { min-height: 100%; padding: 16px 18px;
      display: flex; flex-direction: column; gap: 14px; }
    .msg { font-size: 14.5px; line-height: 1.55; word-wrap: break-word; white-space: pre-wrap; }
    .msg.ai { color: #18181b; max-width: 100%; }
    .msg.ai.typing { color: #a1a1aa; letter-spacing: 1px; }
    .msg.user { background: #f4f4f5; color: #18181b; border-radius: 18px;
      padding: 10px 14px; margin-left: auto; max-width: 88%; }
    .message-content { min-width: 0; }
    .message-response p { margin: 0 0 10px; }
    .message-response p:last-child { margin-bottom: 0; }
    .msg code { background: #f4f4f5; border-radius: 4px; padding: 1px 5px; font-size: 13px; }
    .msg pre { background: #f4f4f5; border-radius: 10px; padding: 10px 12px; overflow-x: auto; margin: 0; }
    .msg pre code { background: none; padding: 0; white-space: pre-wrap; }
    .tool-list { margin-bottom: 10px; display: flex; flex-direction: column; gap: 8px; }
    .agent-meta { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px; color: #71717a;
      font-size: 12px; line-height: 1.3; }
    .route { border: 1px solid #e4e4e7; border-radius: 999px; padding: 4px 8px;
      background: #fafafa; color: #71717a; font-size: 12px; line-height: 1.3; width: fit-content; }
    .tool { border: 1px solid #e4e4e7; border-radius: 10px; background: #fafafa; overflow: hidden; }
    .tool-header { display: flex; align-items: center; justify-content: space-between; gap: 10px;
      padding: 8px 10px; cursor: pointer; list-style: none; }
    .tool-header::-webkit-details-marker { display: none; }
    .tool-title { display: inline-flex; align-items: center; gap: 6px; color: #3f3f46;
      font-size: 12.5px; font-weight: 600; min-width: 0; }
    .tool-title svg, .tool-badge svg { width: 14px; height: 14px; flex: 0 0 auto; }
    .tool-badge { display: inline-flex; align-items: center; gap: 5px; border-radius: 999px;
      background: #f4f4f5; color: #52525b; padding: 3px 7px; font-size: 11.5px; white-space: nowrap; }
    .tool-badge.output-available { color: #166534; background: #dcfce7; }
    .tool-badge.output-error { color: #991b1b; background: #fee2e2; }
    .tool-content { border-top: 1px solid #e4e4e7; padding: 8px 10px; }
    .tool-error { color: #991b1b; font-size: 12px; white-space: pre-wrap; }
    .composer { display: grid; grid-template-columns: 1fr auto; align-items: end; gap: 8px;
      padding: 12px 14px; border-top: 1px solid #f0f0f1; }
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
    .prompt-footer { grid-column: 1 / -1; display: flex; align-items: center;
      justify-content: space-between; gap: 10px; color: #a1a1aa; font-size: 11.5px; }
    .prompt-hint { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
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
