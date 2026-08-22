import { expect, test, type Page, type Route } from "@playwright/test";

// The /playground route (agent-manager's built-in demo page) serves demo.html
// with the widget embedded. These tests cover the route wiring plus a full
// user round-trip on that page; deep widget behavior lives in widget.spec.ts.

async function mockConversationApi(page: Page) {
  const calls: string[] = [];

  await page.route("**/conversations", async (route) => {
    const method = route.request().method();
    calls.push(`${method} ${new URL(route.request().url()).pathname}`);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body:
        method === "GET"
          ? JSON.stringify({ items: [], next_cursor: null })
          : JSON.stringify({ conversation_id: "conv-playground", session_id: "conv-playground" }),
    });
  });

  await page.route(/\/conversations\?/, async (route) => {
    calls.push(`GET ${new URL(route.request().url()).pathname}`);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], next_cursor: null }),
    });
  });

  await page.route("**/conversations/*/messages", async (route: Route) => {
    calls.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });

  await page.route("**/conversations/*/messages/stream", async (route: Route) => {
    const request = route.request();
    calls.push(`${request.method()} ${new URL(request.url()).pathname}`);
    const body = JSON.parse(request.postData() || "{}") as { message?: string };
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: [
        `event: answer_delta\ndata: ${JSON.stringify({ type: "answer_delta", content: `Echo: ${body.message || ""}` })}`,
        `event: final\ndata: ${JSON.stringify({ type: "final", content: `Echo: ${body.message || ""}`, route: [], used_tools: [] })}`,
        "event: done\ndata: [DONE]",
        "",
      ].join("\n\n"),
    });
  });

  return calls;
}

async function widget(page: Page) {
  const handle = await page.locator("agent-chat").elementHandle();
  if (!handle) throw new Error("agent-chat element not found");
  return handle;
}

async function shadowText(page: Page, selector: string) {
  const handle = await widget(page);
  return await handle.evaluate((element, selector) => {
    return element.shadowRoot?.querySelector(selector)?.textContent || "";
  }, selector);
}

async function shadowClassContains(page: Page, selector: string, className: string) {
  const handle = await widget(page);
  return await handle.evaluate(
    (element, { selector, className }) => {
      return element.shadowRoot?.querySelector(selector)?.classList.contains(className) || false;
    },
    { selector, className },
  );
}

async function shadowClick(page: Page, selector: string) {
  const handle = await widget(page);
  await handle.evaluate((element, selector) => {
    element.shadowRoot?.querySelector<HTMLElement>(selector)?.click();
  }, selector);
}

async function shadowFill(page: Page, selector: string, value: string) {
  const handle = await widget(page);
  await handle.evaluate(
    (element, { selector, value }) => {
      const target = element.shadowRoot?.querySelector<HTMLTextAreaElement>(selector);
      if (!target) throw new Error(`Missing ${selector}`);
      target.value = value;
      target.dispatchEvent(new InputEvent("input", { bubbles: true }));
    },
    { selector, value },
  );
}

test("GET /playground serves the demo page with one embedded widget", async ({ page }) => {
  const response = await page.goto("/playground");

  expect(response?.status()).toBe(200);
  expect(response?.headers()["content-type"]).toContain("text/html");
  await expect(page).toHaveTitle(/embed example/);
  await expect(page.getByRole("heading", { name: "Drop-in chat widget" })).toBeVisible();
  await expect(page.locator("agent-chat")).toHaveCount(1);
});

test("GET /widget.js is served as JavaScript for third-party embeds", async ({ page }) => {
  const response = await page.request.get("/widget.js");

  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toContain("javascript");
  expect((await response.body()).length).toBeGreaterThan(0);
});

test("playground widget mounts cleanly and opens with its configured greeting", async ({
  page,
}) => {
  const consoleMessages: string[] = [];
  const pageErrors: string[] = [];
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) consoleMessages.push(message.text());
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await mockConversationApi(page);
  await page.goto("/playground");

  await expect.poll(() => page.evaluate(() => Boolean(customElements.get("agent-chat")))).toBe(true);
  await shadowClick(page, ".launcher");
  await expect.poll(() => shadowClassContains(page, ".panel", "open")).toBe(true);
  await expect.poll(() => shadowText(page, ".header")).toContain("Support");
  await expect.poll(() => shadowText(page, ".messages")).toContain("How can I help you today?");

  expect(consoleMessages).toEqual([]);
  expect(pageErrors).toEqual([]);
});

test("playground chat round-trip: send a message, get the streamed answer", async ({ page }) => {
  const calls = await mockConversationApi(page);
  await page.goto("/playground");

  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "hello from the playground");
  await page.keyboard.press("Enter");

  await expect.poll(() => shadowText(page, ".messages")).toContain("hello from the playground");
  await expect.poll(() => shadowText(page, ".messages")).toContain("Echo: hello from the playground");
  expect(calls).toContain("POST /conversations");
  expect(calls).toContain("POST /conversations/conv-playground/messages/stream");
});
