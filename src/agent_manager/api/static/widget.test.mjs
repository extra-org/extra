// Run: node src/agent_manager/api/static/widget.test.mjs
import assert from "node:assert";

class FakeClassList {
  constructor(element) {
    this.element = element;
  }
  values() {
    return this.element.className.split(/\s+/).filter(Boolean);
  }
  contains(name) {
    return this.values().includes(name);
  }
  add(name) {
    if (!this.contains(name)) this.element.className = [...this.values(), name].join(" ");
  }
  remove(name) {
    this.element.className = this.values().filter((value) => value !== name).join(" ");
  }
  toggle(name, force) {
    const enabled = force ?? !this.contains(name);
    if (enabled) this.add(name);
    else this.remove(name);
    return enabled;
  }
}

class FakeElement {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.localName = tagName.toLowerCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = new Map();
    this.style = {};
    this.className = "";
    this.classList = new FakeClassList(this);
    this.eventListeners = new Map();
    this.textContent = "";
    this.innerHTML = "";
    this.value = "";
    this.placeholder = "";
    this.rows = 0;
    this.disabled = false;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.isConnected = false;
  }
  setAttribute(name, value) {
    const old = this.getAttribute(name);
    this.attributes.set(name, String(value));
    if (this.constructor.observedAttributes?.includes(name)) {
      this.attributeChangedCallback?.(name, old, String(value));
    }
  }
  getAttribute(name) {
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }
  appendChild(child) {
    this.children.push(child);
    child.parentNode = this;
    child.setConnected?.(this.isConnected);
    return child;
  }
  replaceChildren(...children) {
    for (const child of this.children) child.setConnected?.(false);
    this.children = [];
    for (const child of children) this.appendChild(child);
  }
  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    this.parentNode = null;
    this.setConnected?.(false);
  }
  setConnected(value) {
    if (this.isConnected === value) return;
    this.isConnected = value;
    if (value) this.connectedCallback?.();
    else this.disconnectedCallback?.();
    for (const child of this.children) child.setConnected?.(value);
    this.shadowRoot?.setConnected?.(value);
  }
  attachShadow() {
    this.shadowRoot = new FakeShadowRoot(this);
    this.shadowRoot.setConnected(this.isConnected);
    return this.shadowRoot;
  }
  addEventListener(type, handler) {
    const handlers = this.eventListeners.get(type) || new Set();
    handlers.add(handler);
    this.eventListeners.set(type, handlers);
  }
  removeEventListener(type, handler) {
    this.eventListeners.get(type)?.delete(handler);
  }
  dispatchEvent(event) {
    event.target ??= this;
    event.stopPropagation ??= () => {};
    event.preventDefault ??= () => {};
    for (const handler of this.eventListeners.get(event.type) || []) handler(event);
  }
  click() {
    this.dispatchEvent({ type: "click" });
  }
  querySelector(selector) {
    return findFirst(this, selector);
  }
  querySelectorAll(selector) {
    return findAll(this, selector);
  }
}

class FakeShadowRoot extends FakeElement {
  constructor(host) {
    super("#shadow-root");
    this.host = host;
  }
}

class FakeHTMLElement extends FakeElement {}

class FakeDocument extends FakeElement {
  constructor() {
    super("#document");
    this.readyState = "loading";
    this.body = new FakeElement("body");
  }
  createElement(tagName) {
    const Constructor = customElements.get(tagName);
    const element = Constructor ? new Constructor() : new FakeElement(tagName);
    element.localName = tagName.toLowerCase();
    element.tagName = tagName.toUpperCase();
    return element;
  }
  querySelector(selector) {
    return this.body.querySelector(selector);
  }
}

class FakeCustomElements {
  constructor() {
    this.registry = new Map();
    this.defineCount = 0;
  }
  define(name, constructor) {
    if (this.registry.has(name)) throw new Error(`already defined: ${name}`);
    this.defineCount += 1;
    this.registry.set(name, constructor);
  }
  get(name) {
    return this.registry.get(name);
  }
}

class FakeStorage {
  constructor() {
    this.values = new Map();
  }
  getItem(key) {
    return this.values.has(key) ? this.values.get(key) : null;
  }
  setItem(key, value) {
    this.values.set(key, String(value));
  }
  removeItem(key) {
    this.values.delete(key);
  }
  clear() {
    this.values.clear();
  }
}

function matches(element, selector) {
  if (selector.startsWith(".")) return element.classList.contains(selector.slice(1));
  return element.localName === selector.toLowerCase();
}

function findFirst(root, selector) {
  for (const child of root.children) {
    if (matches(child, selector)) return child;
    const found = findFirst(child, selector);
    if (found) return found;
  }
  return null;
}

function findAll(root, selector, found = []) {
  for (const child of root.children) {
    if (matches(child, selector)) found.push(child);
    findAll(child, selector, found);
  }
  return found;
}

function installDom() {
  globalThis.customElements = new FakeCustomElements();
  globalThis.HTMLElement = FakeHTMLElement;
  globalThis.document = new FakeDocument();
  globalThis.window = { agentChatConfig: undefined };
  globalThis.localStorage = new FakeStorage();
}

function resetPage() {
  document.body.replaceChildren();
  localStorage.clear();
  window.agentChatConfig = undefined;
}

function jsonResponse(body, ok = true, status = ok ? 200 : 500) {
  return { ok, status, json: async () => body };
}

async function flush() {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

installDom();
const widget = await import(`./widget.js?test=${Date.now()}`);
const {
  AgentChatClient,
  autoMountAgentChat,
  conversationStorageKey,
  defineAgentChat,
  escapeHtml,
  formatAssistantText,
  parseConfig,
} = widget;

function createChat(attrs = {}) {
  const element = document.createElement("agent-chat");
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
  document.body.appendChild(element);
  return element;
}

assert.equal(escapeHtml("<script>alert(1)</script>"), "&lt;script&gt;alert(1)&lt;/script&gt;");
assert.equal(escapeHtml(`it's "quoted" & <b>`), "it&#39;s &quot;quoted&quot; &amp; &lt;b&gt;");
assert.equal(formatAssistantText("**bold** and `code`"), "<strong>bold</strong> and <code>code</code>");
assert.equal(formatAssistantText("line1\nline2"), "line1\nline2");
assert.ok(formatAssistantText("```\nconst x = 1;\n```").includes("<pre><code>const x = 1;</code></pre>"));
assert.equal(formatAssistantText("<img onerror=alert(1)>"), "&lt;img onerror=alert(1)&gt;");
assert.equal(formatAssistantText("<b>raw</b>"), "&lt;b&gt;raw&lt;/b&gt;");

assert.ok(customElements.get("agent-chat"), "custom element is registered");
const definesBefore = customElements.defineCount;
defineAgentChat("https://ignored.example");
assert.equal(customElements.defineCount, definesBefore, "defineAgentChat is idempotent");

{
  const element = new FakeElement("agent-chat");
  const cfg = parseConfig(element, "https://widget.example");
  assert.deepEqual(cfg, {
    endpoint: "https://widget.example",
    title: "Assistant",
    color: "#18181b",
    greeting: "",
    position: "bottom-right",
    avatar: "",
    mode: "floating",
    user: "",
  });
}

{
  const element = new FakeElement("agent-chat");
  element.setAttribute("endpoint", "https://api.example/");
  element.setAttribute("title", "Support");
  element.setAttribute("color", "#7c3aed");
  element.setAttribute("greeting", "Hi");
  element.setAttribute("position", "bottom-left");
  element.setAttribute("avatar", "https://cdn.example/a.png");
  element.setAttribute("mode", "inline");
  const cfg = parseConfig(element, "https://widget.example");
  assert.equal(cfg.endpoint, "https://api.example");
  assert.equal(cfg.title, "Support");
  assert.equal(cfg.color, "#7c3aed");
  assert.equal(cfg.greeting, "Hi");
  assert.equal(cfg.position, "bottom-left");
  assert.equal(cfg.avatar, "https://cdn.example/a.png");
  assert.equal(cfg.mode, "inline");
}

{
  const element = new FakeElement("agent-chat");
  element.setAttribute("color", "url(javascript:alert(1))");
  element.setAttribute("position", "top-left");
  element.setAttribute("mode", "sideways");
  const cfg = parseConfig(element, "https://widget.example");
  assert.equal(cfg.color, "#18181b");
  assert.equal(cfg.position, "bottom-right");
  assert.equal(cfg.mode, "floating");
}

resetPage();
window.agentChatConfig = { title: "Auto", color: "#111111", endpoint: "https://api.example/" };
autoMountAgentChat();
assert.equal(document.body.querySelectorAll("agent-chat").length, 1);
const mounted = document.body.querySelector("agent-chat");
assert.equal(mounted.getAttribute("title"), "Auto");
assert.equal(mounted.getAttribute("endpoint"), "https://api.example/");
autoMountAgentChat();
assert.equal(document.body.querySelectorAll("agent-chat").length, 1, "auto-mount does not duplicate");

resetPage();
const fetchCalls = [];
globalThis.fetch = async (url, options = {}) => {
  fetchCalls.push({ url, options });
  if (url.endsWith("/conversations")) return jsonResponse({ conversation_id: "conv-1" });
  if (url.endsWith("/messages")) return jsonResponse({ answer: "hello back" });
  throw new Error(`unexpected fetch: ${url}`);
};
let client = new AgentChatClient("https://api.example");
const conversationId = await client.createConversation();
assert.equal(conversationId, "conv-1");
const sendResponse = await client.sendMessage(conversationId, "hello");
assert.equal(fetchCalls[0].url, "https://api.example/conversations");
assert.equal(fetchCalls[1].url, "https://api.example/conversations/conv-1/messages");
assert.equal(JSON.parse(fetchCalls[1].options.body).message, "hello");
assert.equal(sendResponse.answer, "hello back");

resetPage();
globalThis.fetch = async (url) => {
  if (url.endsWith("/conv-stored/messages")) {
    return jsonResponse([{ role: "assistant", content: "old answer" }]);
  }
  throw new Error(`unexpected fetch: ${url}`);
};
client = new AgentChatClient("https://api.example");
const history = await client.getMessages("conv-stored");
assert.deepEqual(history, [{ role: "assistant", content: "old answer" }]);

resetPage();
globalThis.fetch = async (url) => {
  if (url.endsWith("/conversations")) return jsonResponse({ conversation_id: "conv-2" });
  return jsonResponse({}, false, 500);
};
client = new AgentChatClient("https://api.example");
assert.ok(client);
await assert.rejects(() => client.sendMessage("conv-2", "break"), /HTTP 500/);

resetPage();
const feedbackCalls = [];
globalThis.fetch = async (url, options = {}) => {
  feedbackCalls.push({ url, options });
  if (url.endsWith("/feedback")) return jsonResponse({ message_id: "msg-123", feedback: "thumbs_up" });
  throw new Error(`unexpected fetch: ${url}`);
};
client = new AgentChatClient("https://api.example");
await client.recordFeedback("conv-1", "msg-123", "thumbs_up");
assert.equal(feedbackCalls[0].url, "https://api.example/conversations/conv-1/messages/msg-123/feedback");
assert.equal(JSON.parse(feedbackCalls[0].options.body).feedback, "thumbs_up");

console.log("widget self-check: OK");
