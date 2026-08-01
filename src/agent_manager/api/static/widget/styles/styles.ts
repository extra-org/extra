import type { AgentChatConfig } from "../types";

export function styles(config: AgentChatConfig): string {
  const side = config.position === "bottom-left" ? "left" : "right";
  return `
    :host { all: initial; }
    * { box-sizing: border-box; font-family: -apple-system, system-ui, sans-serif; }
    .react-mount,
    .agent-chat-react { display: contents; }
    .launcher {
      position: fixed; bottom: 16px; ${side}: 16px; width: 44px; height: 44px;
      border: 0; border-radius: 50%; background: ${config.color}; color: #fff; cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 10px 15px -3px rgba(0,0,0,.1), 0 4px 6px -4px rgba(0,0,0,.1);
      z-index: 2147483000; transition: transform .15s ease-out; }
    .launcher:hover { transform: scale(1.05); }
    .launcher:active { transform: scale(.96); }
    .launcher svg { position: absolute; width: 24px; height: 24px;
      transition: scale .2s cubic-bezier(.2,0,0,1), opacity .2s cubic-bezier(.2,0,0,1),
      filter .2s cubic-bezier(.2,0,0,1); }
    .launcher .icon-bot { scale: 1; opacity: 1; filter: blur(0); }
    .launcher .icon-chevron { scale: .25; opacity: 0; filter: blur(4px); }
    .launcher.open .icon-bot { scale: .25; opacity: 0; filter: blur(4px); }
    .launcher.open .icon-chevron { scale: 1; opacity: 1; filter: blur(0); }
    .panel {
      position: fixed; bottom: 76px; ${side}: 16px; width: 440px; height: 680px;
      max-width: calc(100vw - 2rem); max-height: calc(100vh - 92px);
      background: #fff; border-radius: 40px; overflow: hidden;
      border: 1px solid rgba(228,228,231,.6);
      display: flex; flex-direction: column;
      box-shadow: 0 20px 25px -5px rgba(0,0,0,.1), 0 8px 10px -6px rgba(0,0,0,.1);
      transform-origin: bottom ${side};
      opacity: 0; transform: translateY(8px) scale(.95); pointer-events: none;
      transition: opacity .2s cubic-bezier(.32,.72,0,1), transform .2s cubic-bezier(.32,.72,0,1);
      z-index: 2147483000; }
    .panel.open { opacity: 1; transform: none; pointer-events: auto;
      transition-duration: .3s; }
    .panel.open .composer {
      animation: aui-footer-in .3s .1s cubic-bezier(.32,.72,0,1) backwards; }
    @keyframes aui-footer-in {
      from { opacity: 0; transform: translateY(8px); } }
    .panel.inline { position: static; opacity: 1; transform: none; pointer-events: auto;
      box-shadow: 0 4px 18px rgba(0,0,0,.12); }
    .header { background: #fff; color: #18181b; padding: 12px 12px 12px 14px; font-weight: 600;
      font-size: 14px; display: flex; align-items: center; gap: 6px;
      border-bottom: 1px solid #f0f0f1; }
    .header .dot { width: 22px; height: 22px; border-radius: 50%; flex: 0 0 auto;
      background: ${config.color}; background-size: cover; background-position: center; }
    .header .title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .header-btn { background: transparent; border: 0; color: #71717a; cursor: pointer;
      padding: 0; width: 30px; height: 30px; border-radius: 8px; flex: 0 0 auto;
      display: flex; align-items: center; justify-content: center;
      transition: background .12s, color .12s; }
    .header-btn:hover { background: #f4f4f5; color: #18181b; }
    .header-btn svg { width: 17px; height: 17px; }
    .close { background: transparent; border: 0; color: #71717a; cursor: pointer;
      padding: 0; width: 30px; height: 30px; border-radius: 50%;
      display: flex; align-items: center; justify-content: center; transition: background .12s; }
    .close:hover { background: #f4f4f5; color: #18181b; }
    .close svg { width: 16px; height: 16px; }
    .body { flex: 1; min-height: 0; position: relative; display: flex; flex-direction: column; background: #fff; }
    .thread-drawer { position: absolute; inset: 0; z-index: 3; background: #fff;
      display: flex; flex-direction: column; transform: translateX(-100%);
      opacity: 0; pointer-events: none;
      transition: transform .25s cubic-bezier(.32,.72,0,1), opacity .25s ease; }
    .thread-drawer.open { transform: none; opacity: 1; pointer-events: auto; }
    .thread-drawer-head { display: flex; align-items: center; justify-content: space-between;
      padding: 10px 12px 10px 18px; font-weight: 600; font-size: 14px; color: #18181b;
      border-bottom: 1px solid #f0f0f1; flex: 0 0 auto; }
    .thread-new { display: flex; align-items: center; gap: 8px; margin: 12px 14px 4px;
      padding: 10px 14px; border: 1px solid #e4e4e7; border-radius: 12px; background: #fff;
      color: #18181b; font-size: 14px; font-weight: 500; cursor: pointer; font-family: inherit;
      transition: background .12s; flex: 0 0 auto; }
    .thread-new:hover { background: #f4f4f5; }
    .thread-new svg { width: 16px; height: 16px; flex: 0 0 auto; }
    .thread-list { flex: 1; min-height: 0; overflow-y: auto; padding: 6px 10px 14px;
      display: flex; flex-direction: column; gap: 2px;
      scrollbar-width: thin; scrollbar-color: #d4d4d8 transparent; }
    .thread-item { text-align: left; border: 0; background: transparent; cursor: pointer;
      padding: 10px 12px; border-radius: 10px; font-size: 13.5px; color: #3f3f46;
      font-family: inherit; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      transition: background .12s; flex: 0 0 auto; }
    .thread-item:hover { background: #f4f4f5; }
    .thread-item.active { background: #f4f4f5; color: #18181b; font-weight: 600; }
    .thread-empty { color: #a1a1aa; font-size: 13px; text-align: center; padding: 24px 12px; margin: 0; }
    .messages { flex: 1; min-height: 0; overflow-y: auto;
      scrollbar-width: thin; scrollbar-color: #d4d4d8 transparent; }
    .messages::-webkit-scrollbar { width: 10px; }
    .messages::-webkit-scrollbar-track { background: transparent; }
    .messages::-webkit-scrollbar-thumb { background: #d4d4d8; border-radius: 999px;
      border: 3px solid transparent; background-clip: content-box; }
    .messages::-webkit-scrollbar-thumb:hover { background: #a1a1aa; background-clip: content-box; }
    .conversation-content { min-height: 100%; padding: 16px 18px;
      display: flex; flex-direction: column; gap: 14px; }
    .welcome { flex: 1; display: flex; flex-direction: column; align-items: center;
      justify-content: center; gap: 16px; text-align: center; padding: 32px 24px;
      animation: aui-footer-in .3s .05s cubic-bezier(.32,.72,0,1) backwards; }
    .welcome-avatar { width: 48px; height: 48px; border-radius: 50%; flex: 0 0 auto;
      background: ${config.color}; color: #fff;
      display: flex; align-items: center; justify-content: center; }
    .welcome-avatar svg { width: 26px; height: 26px; }
    .welcome-title { margin: 0; font-size: 17px; font-weight: 600; color: #18181b; line-height: 1.4; }
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
    .msg-actions { display: flex; gap: 4px; margin-top: 6px;
      opacity: 0; transition: opacity .15s ease; }
    .msg.ai:hover .msg-actions, .msg-actions:focus-within { opacity: 1; }
    .msg-action { display: inline-flex; align-items: center; justify-content: center;
      width: 26px; height: 26px; border: 0; border-radius: 7px; background: transparent;
      color: #71717a; cursor: pointer; transition: background .12s, color .12s; }
    .msg-action:hover { background: #f4f4f5; color: #18181b; }
    .msg-action svg { width: 15px; height: 15px; animation: aui-icon-in .15s ease; }
    @keyframes aui-icon-in { from { opacity: 0; transform: scale(.75); } }
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
    .msg-error { margin-top: 2px; border: 1px solid #fecaca; background: #fef2f2;
      color: #b91c1c; border-radius: 8px; padding: 10px 12px; font-size: 13.5px;
      line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 2;
      -webkit-box-orient: vertical; overflow: hidden; }
    .thinking { display: inline-flex; gap: 4px; color: #a1a1aa; }
    .thinking-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor;
      animation: aui-dot 1.4s ease-in-out infinite; }
    .thinking-dot:nth-child(2) { animation-delay: .2s; }
    .thinking-dot:nth-child(3) { animation-delay: .4s; }
    @keyframes aui-dot {
      0%, 100% { transform: scale(.8); opacity: .5; }
      50% { transform: scale(1.2); opacity: 1; }
    }
    .composer { display: grid; grid-template-columns: 1fr auto; align-items: end; gap: 8px;
      padding: 12px 14px; border-top: 1px solid #f0f0f1; }
    .input-wrap { min-width: 0; display: flex; border-radius: 20px; background: #fff;
      overflow: hidden; box-shadow: inset 0 0 0 1px #e4e4e7; transition: box-shadow .15s ease; }
    .input-wrap:focus-within { box-shadow: inset 0 0 0 1px ${config.color},
      0 0 0 3px color-mix(in srgb, ${config.color} 14%, transparent); }
    .input { flex: 1; min-width: 0; resize: none; max-height: 140px; overflow-y: hidden;
      border: 0; padding: 12px 16px; font-size: 15px; color: #18181b;
      font-family: inherit; background: transparent; outline: none;
      scrollbar-width: thin; scrollbar-color: #d4d4d8 transparent; }
    .input::-webkit-scrollbar { width: 10px; }
    .input::-webkit-scrollbar-track { background: transparent; margin: 8px 0; }
    .input::-webkit-scrollbar-thumb { background: #d4d4d8; border-radius: 999px;
      border: 3px solid transparent; background-clip: content-box; }
    .input::-webkit-scrollbar-thumb:hover { background: #a1a1aa; background-clip: content-box; }
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
    .footer-start { display: flex; align-items: center; gap: 8px; min-width: 0; }
    .budget-meter { position: relative; display: inline-flex; align-items: center; gap: 5px;
      flex: 0 0 auto; color: #71717a; font-size: 11.5px; cursor: default; outline: none; }
    .budget-ring { transform: rotate(-90deg); }
    .budget-ring-track { stroke: #e4e4e7; }
    .budget-ring-value { stroke: #3f3f46;
      transition: stroke-dashoffset .3s ease, stroke .3s ease; }
    .budget-percent { font-variant-numeric: tabular-nums; }
    .budget-meter.warning .budget-ring-value { stroke: #f59e0b; }
    .budget-meter.warning .budget-percent { color: #b45309; }
    .budget-meter.critical .budget-ring-value { stroke: #ef4444; }
    .budget-meter.critical .budget-percent { color: #b91c1c; }
    .budget-popover { position: absolute; bottom: calc(100% + 8px); left: 0; width: 200px;
      background: #fff; color: #18181b; border: 1px solid #e4e4e7; border-radius: 10px;
      padding: 10px 12px; box-shadow: 0 10px 25px rgba(0,0,0,.12);
      opacity: 0; transform: translateY(4px); pointer-events: none;
      transition: opacity .15s ease, transform .15s ease; z-index: 5; }
    .budget-meter:hover .budget-popover, .budget-meter:focus-visible .budget-popover {
      opacity: 1; transform: none; }
    .budget-popover-head { display: flex; align-items: baseline; justify-content: space-between;
      gap: 12px; font-size: 12px; font-weight: 600; white-space: nowrap; }
    .budget-popover-count { color: #71717a; font-weight: 400; font-variant-numeric: tabular-nums; }
    .budget-bar { display: block; margin-top: 8px; height: 4px; background: #f4f4f5;
      border-radius: 999px; overflow: hidden; }
    .budget-bar-fill { display: block; height: 100%; background: #3f3f46;
      border-radius: 999px; transition: width .3s ease; }
    .budget-meter.warning .budget-bar-fill { background: #f59e0b; }
    .budget-meter.critical .budget-bar-fill { background: #ef4444; }
    .powered { text-align: center; padding: 0 14px 10px; color: #a1a1aa;
      font-size: 11px; letter-spacing: .01em; }
    @media (prefers-reduced-motion: reduce) {
      .launcher,
      .launcher svg,
      .close,
      .send,
      .panel {
        transition: none;
      }
      .launcher:hover { transform: none; }
      .panel.open .composer { animation: none; }
      .welcome { animation: none; }
      .msg-action svg { animation: none; }
      .budget-ring-value, .budget-bar-fill, .budget-popover { transition: none; }
      .thread-drawer { transition: none; }
      .thinking-dot { animation: none; }

    }
    @media (max-width: 480px) {
      .panel:not(.inline) { width: 100vw; height: 100dvh; max-height: 100dvh;
        bottom: 0; ${side}: 0; border-radius: 0; }
    }`;
}
