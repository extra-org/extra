import { expect, test, type Page, type Route } from "@playwright/test";

const history: Record<string, Array<{ role: string; content: string; created_at: string }>> = {};

async function mockConversationApi(
  page: Page,
  options: {
    failSend?: boolean;
    threads?: Array<{ conversation_id: string; title: string | null; last_message_at: string | null }>;
  } = {},
) {
  const calls: string[] = [];

  await page.route(/\/conversations\?/, async (route) => {
    calls.push(`GET ${new URL(route.request().url()).pathname}`);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(options.threads ?? []),
    });
  });

  await page.route("**/conversations", async (route) => {
    calls.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ conversation_id: "conv-smoke", session_id: "conv-smoke" }),
    });
  });

  await page.route("**/conversations/*/messages/stream", async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const conversationId = url.pathname.split("/")[2] || "conv-smoke";
    calls.push(`${request.method()} ${url.pathname}`);

    if (options.failSend) {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "test failure" }),
      });
      return;
    }

    const body = JSON.parse(request.postData() || "{}") as { message?: string };
    const now = new Date("2026-06-28T00:00:00.000Z").toISOString();
    history[conversationId] = [
      ...(history[conversationId] || []),
      { role: "user", content: body.message || "", created_at: now },
      { role: "assistant", content: `Echo: ${body.message || ""}`, created_at: now },
    ];

    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: [
        `event: answer_delta\ndata: ${JSON.stringify({ type: "answer_delta", content: "Echo: " })}`,
        `event: answer_delta\ndata: ${JSON.stringify({ type: "answer_delta", content: body.message || "" })}`,
        `event: final\ndata: ${JSON.stringify({ type: "final", content: `Echo: ${body.message || ""}`, route: [], used_tools: [] })}`,
        "event: done\ndata: [DONE]",
        "",
      ].join("\n\n"),
    });
  });

  await page.route("**/conversations/*/messages", async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const conversationId = url.pathname.split("/")[2] || "conv-smoke";
    calls.push(`${request.method()} ${url.pathname}`);

    if (request.method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(history[conversationId] || []),
      });
      return;
    }

    if (options.failSend) {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "test failure" }),
      });
      return;
    }

    const body = JSON.parse(request.postData() || "{}") as { message?: string };
    const now = new Date("2026-06-28T00:00:00.000Z").toISOString();
    history[conversationId] = [
      ...(history[conversationId] || []),
      { role: "user", content: body.message || "", created_at: now },
      { role: "assistant", content: `Echo: ${body.message || ""}`, created_at: now },
    ];

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ answer: `Echo: ${body.message || ""}`, visited: [], used_tools: [] }),
    });
  });

  return calls;
}

async function mockConversationApiWithStaleConversation(page: Page) {
  const calls: string[] = [];
  let created = false;

  await page.route("**/conversations", async (route) => {
    calls.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
    created = true;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ conversation_id: "conv-fresh", session_id: "conv-fresh" }),
    });
  });

  await page.route("**/conversations/*/messages", async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const conversationId = url.pathname.split("/")[2] || "";
    calls.push(`${request.method()} ${url.pathname}`);

    if (conversationId === "conv-stale") {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "conversation not found" }),
      });
      return;
    }

    if (conversationId !== "conv-fresh" || !created) {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "unexpected conversation" }),
      });
      return;
    }

    if (request.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) });
      return;
    }

    const body = JSON.parse(request.postData() || "{}") as { message?: string };
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ answer: `Recovered: ${body.message || ""}`, visited: [], used_tools: [] }),
    });
  });

  await page.route("**/conversations/*/messages/stream", async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const conversationId = url.pathname.split("/")[2] || "";
    calls.push(`${request.method()} ${url.pathname}`);

    if (conversationId === "conv-stale") {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "conversation not found" }),
      });
      return;
    }

    const body = JSON.parse(request.postData() || "{}") as { message?: string };
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: [
        `event: answer_delta\ndata: ${JSON.stringify({ type: "answer_delta", content: "Recovered: " })}`,
        `event: final\ndata: ${JSON.stringify({ type: "final", content: `Recovered: ${body.message || ""}`, route: [], used_tools: [] })}`,
        "event: done\ndata: [DONE]",
        "",
      ].join("\n\n"),
    });
  });

  return calls;
}

async function widget(page: Page, index = 0) {
  const handle = await page.locator("agent-chat").nth(index).elementHandle();
  if (!handle) throw new Error("agent-chat element not found");
  return handle;
}

async function shadowText(page: Page, selector: string, index = 0) {
  const handle = await widget(page, index);
  return await handle.evaluate((element, selector) => {
    return element.shadowRoot?.querySelector(selector)?.textContent || "";
  }, selector);
}

async function shadowExists(page: Page, selector: string, index = 0) {
  const handle = await widget(page, index);
  return await handle.evaluate((element, selector) => {
    return Boolean(element.shadowRoot?.querySelector(selector));
  }, selector);
}

async function shadowAttribute(page: Page, selector: string, attribute: string, index = 0) {
  const handle = await widget(page, index);
  return await handle.evaluate(
    (element, { selector, attribute }) => {
      return element.shadowRoot?.querySelector(selector)?.getAttribute(attribute) || "";
    },
    { selector, attribute },
  );
}

async function shadowClassContains(page: Page, selector: string, className: string, index = 0) {
  const handle = await widget(page, index);
  return await handle.evaluate(
    (element, { selector, className }) => {
      return element.shadowRoot?.querySelector(selector)?.classList.contains(className) || false;
    },
    { selector, className },
  );
}

async function shadowActiveMatches(page: Page, selector: string, index = 0) {
  const handle = await widget(page, index);
  return await handle.evaluate((element, selector) => {
    return element.shadowRoot?.activeElement === element.shadowRoot?.querySelector(selector);
  }, selector);
}

async function shadowComputedStyle(page: Page, selector: string, property: string, index = 0) {
  const handle = await widget(page, index);
  return await handle.evaluate(
    (element, { selector, property }) => {
      const target = element.shadowRoot?.querySelector(selector);
      if (!target) throw new Error(`Missing ${selector}`);
      return getComputedStyle(target).getPropertyValue(property);
    },
    { selector, property },
  );
}

async function shadowClick(page: Page, selector: string, index = 0) {
  const handle = await widget(page, index);
  await handle.evaluate((element, selector) => {
    const target = element.shadowRoot?.querySelector<HTMLElement>(selector);
    target?.click();
  }, selector);
}

async function shadowFocus(page: Page, selector: string, index = 0) {
  const handle = await widget(page, index);
  await handle.evaluate((element, selector) => {
    const target = element.shadowRoot?.querySelector<HTMLElement>(selector);
    if (!target) throw new Error(`Missing ${selector}`);
    target.focus();
  }, selector);
}

async function shadowFill(page: Page, selector: string, value: string, index = 0) {
  const handle = await widget(page, index);
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

async function shadowValue(page: Page, selector: string, index = 0) {
  const handle = await widget(page, index);
  return await handle.evaluate((element, selector) => {
    const target = element.shadowRoot?.querySelector<HTMLTextAreaElement>(selector);
    if (!target) throw new Error(`Missing ${selector}`);
    return target.value;
  }, selector);
}

test.beforeEach(() => {
  for (const key of Object.keys(history)) delete history[key];
});

test("floating widget loads, registers, opens, closes, and shows greeting", async ({ page }) => {
  const consoleMessages: string[] = [];
  const pageErrors: string[] = [];
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) consoleMessages.push(message.text());
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await mockConversationApi(page);
  await page.goto("/widget-demo.html");

  await expect(page.locator("agent-chat")).toHaveCount(1);
  await expect.poll(() => page.evaluate(() => Boolean(customElements.get("agent-chat")))).toBe(true);
  await expect.poll(async () => (await widget(page)).evaluate((element) => Boolean(element.shadowRoot))).toBe(true);
  await expect.poll(() => shadowExists(page, ".launcher")).toBe(true);
  await expect.poll(() => shadowAttribute(page, ".launcher", "aria-label")).toBe("Open Assistant");
  await expect.poll(() => shadowAttribute(page, ".launcher", "aria-expanded")).toBe("false");
  await expect.poll(() => shadowAttribute(page, ".launcher", "aria-controls")).not.toBe("");
  await expect.poll(() => shadowAttribute(page, ".panel", "role")).toBe("dialog");
  await expect.poll(() => shadowAttribute(page, ".panel", "aria-labelledby")).not.toBe("");
  await expect.poll(() => shadowAttribute(page, ".messages", "aria-live")).toBe("polite");

  await shadowClick(page, ".launcher");
  await expect.poll(() => shadowClassContains(page, ".panel", "open")).toBe(true);
  await expect.poll(() => shadowAttribute(page, ".launcher", "aria-expanded")).toBe("true");
  await expect.poll(() => shadowActiveMatches(page, ".input")).toBe(true);
  await expect.poll(() => shadowText(page, ".messages")).toContain("How can I help you today?");

  await shadowClick(page, ".close");
  await expect.poll(() => shadowClassContains(page, ".panel", "open")).toBe(false);
  await expect.poll(() => shadowAttribute(page, ".launcher", "aria-expanded")).toBe("false");
  await expect.poll(() => shadowActiveMatches(page, ".launcher")).toBe(true);

  expect(consoleMessages).toEqual([]);
  expect(pageErrors).toEqual([]);
});

test("floating widget opens with Enter and Space from the launcher", async ({ page }) => {
  await mockConversationApi(page);
  await page.goto("/widget-demo.html");

  await shadowFocus(page, ".launcher");
  await page.keyboard.press("Enter");
  await expect.poll(() => shadowClassContains(page, ".panel", "open")).toBe(true);
  await expect.poll(() => shadowActiveMatches(page, ".input")).toBe(true);

  await page.keyboard.press("Escape");
  await expect.poll(() => shadowClassContains(page, ".panel", "open")).toBe(false);
  await expect.poll(() => shadowActiveMatches(page, ".launcher")).toBe(true);

  await page.keyboard.press("Space");
  await expect.poll(() => shadowClassContains(page, ".panel", "open")).toBe(true);
  await expect.poll(() => shadowAttribute(page, ".launcher", "aria-expanded")).toBe("true");
});

test("floating controls expose accessible labels", async ({ page }) => {
  await mockConversationApi(page);
  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");

  await expect.poll(() => shadowAttribute(page, ".close", "aria-label")).toBe("Close chat");
  await expect.poll(() => shadowAttribute(page, ".send", "aria-label")).toBe("Send message");
  await expect.poll(() => shadowAttribute(page, ".input", "aria-label")).toBe("Message");
});

test("Tab navigation reaches close, input, and send controls", async ({ page }) => {
  await mockConversationApi(page);
  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");

  await expect.poll(() => shadowActiveMatches(page, ".input")).toBe(true);
  await page.keyboard.press("Shift+Tab");
  await expect.poll(() => shadowActiveMatches(page, ".close")).toBe(true);
  await page.keyboard.press("Tab");
  await expect.poll(() => shadowActiveMatches(page, ".input")).toBe(true);
  await page.keyboard.press("Tab");
  await expect.poll(() => shadowActiveMatches(page, ".send")).toBe(true);
});

test("prefers-reduced-motion removes widget transitions", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockConversationApi(page);
  await page.goto("/widget-demo.html");

  await expect.poll(() => shadowComputedStyle(page, ".panel", "transition-duration")).toBe("0s");
  await expect.poll(() => shadowComputedStyle(page, ".launcher", "transition-duration")).toBe("0s");
});

test("inline mode renders without launcher click", async ({ page }) => {
  await mockConversationApi(page);
  await page.goto("/widget-demo-inline.html");

  await expect(page.locator("agent-chat")).toHaveCount(1);
  await expect.poll(() => shadowClassContains(page, ".panel", "inline")).toBe(true);
  await expect.poll(() => shadowAttribute(page, ".panel", "role")).toBe("region");
  await expect.poll(() => shadowExists(page, ".launcher")).toBe(false);
  await expect.poll(() => shadowText(page, ".messages")).toContain("Inline help is ready.");
  await shadowFocus(page, ".input");
  await page.keyboard.press("Escape");
  await expect.poll(() => shadowClassContains(page, ".panel", "inline")).toBe(true);
});

test("sending a message calls backend, renders assistant answer, stores conversation, and reloads history", async ({
  page,
}) => {
  const calls = await mockConversationApi(page);
  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "hello browser");
  await page.keyboard.press("Enter");

  await expect.poll(() => shadowText(page, ".messages")).toContain("Echo: hello browser");
  await expect.poll(() => shadowActiveMatches(page, ".input")).toBe(true);
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("agent-chat:http://127.0.0.1:8123")))
    .toBe("conv-smoke");
  expect(calls).toContain("POST /conversations");
  expect(calls).toContain("POST /conversations/conv-smoke/messages/stream");

  await page.reload();
  await shadowClick(page, ".launcher");
  await expect.poll(() => shadowText(page, ".messages")).toContain("hello browser");
  await expect.poll(() => shadowText(page, ".messages")).toContain("Echo: hello browser");
  expect(calls).toContain("GET /conversations/conv-smoke/messages");
});

test("Shift+Enter inserts a newline and Enter sends", async ({ page }) => {
  const calls = await mockConversationApi(page);
  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");

  await page.keyboard.type("first line");
  await page.keyboard.press("Shift+Enter");
  await page.keyboard.type("second line");

  await expect.poll(() => shadowValue(page, ".input")).toBe("first line\nsecond line");
  expect(calls).not.toContain("POST /conversations");
  expect(calls).not.toContain("POST /conversations/conv-smoke/messages");
  expect(calls).not.toContain("POST /conversations/conv-smoke/messages/stream");

  await page.keyboard.press("Enter");
  await expect.poll(() => shadowText(page, ".messages")).toContain("Echo: first line\nsecond line");
  expect(calls).toContain("POST /conversations");
  expect(calls).toContain("POST /conversations/conv-smoke/messages/stream");
});

test("backend error renders a user-friendly message", async ({ page }) => {
  await mockConversationApi(page, { failSend: true });
  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "please fail");
  await shadowClick(page, ".send");

  await expect.poll(() => shadowText(page, ".messages")).toContain("Something went wrong. Please try again.");
});

test("context meter shows token usage against the budget after a turn", async ({ page }) => {
  await mockConversationApi(page);
  await page.route("**/conversations/*/usage", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        used_tokens: 900,
        max_tokens: 1000,
        percent: 90,
        severity: "critical",
      }),
    });
  });
  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");

  await shadowFill(page, ".input", "hello");
  await page.keyboard.press("Enter");

  await expect.poll(() => shadowText(page, ".messages")).toContain("Echo: hello");
  await expect.poll(() => shadowExists(page, ".context-meter")).toBe(true);
  await expect.poll(() => shadowText(page, ".context-percent")).toBe("90%");
  await expect.poll(() => shadowClassContains(page, ".context-meter", "critical")).toBe(true);
});

test("context meter stays hidden when no budget is configured", async ({ page }) => {
  await mockConversationApi(page);
  await page.route("**/conversations/*/usage", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ used_tokens: 0, max_tokens: null, percent: 0, severity: "normal" }),
    });
  });
  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "hello");
  await page.keyboard.press("Enter");

  await expect.poll(() => shadowText(page, ".messages")).toContain("Echo: hello");
  await expect.poll(() => shadowExists(page, ".context-meter")).toBe(false);
});

test("thread drawer lists conversations, switches to one, and starts a new chat", async ({ page }) => {
  await mockConversationApi(page, {
    threads: [
      { conversation_id: "conv-old", title: "Older chat", last_message_at: "2026-06-01T00:00:00Z" },
    ],
  });
  await page.route("**/conversations/conv-old/messages", async (route: Route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        { role: "user", content: "old question", created_at: "2026-06-01T00:00:00Z" },
        { role: "assistant", content: "old answer", created_at: "2026-06-01T00:00:00Z" },
      ]),
    });
  });

  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");

  await shadowClick(page, '.header-btn[aria-label="Conversations"]');
  await expect.poll(() => shadowClassContains(page, ".thread-drawer", "open")).toBe(true);
  await expect.poll(() => shadowText(page, ".thread-item")).toContain("Older chat");

  await shadowClick(page, ".thread-item");
  await expect.poll(() => shadowText(page, ".messages")).toContain("old answer");
  await expect.poll(() => shadowClassContains(page, ".thread-drawer", "open")).toBe(false);

  await shadowClick(page, '.header-btn[aria-label="New chat"]');
  await expect.poll(() => shadowText(page, ".messages")).toContain("How can I help you today?");
});

test("stale stored conversation is replaced before sending to the agent", async ({ page }) => {
  const calls = await mockConversationApiWithStaleConversation(page);
  await page.goto("/widget-demo.html");
  await page.evaluate(() => localStorage.setItem("agent-chat:http://127.0.0.1:8123", "conv-stale"));

  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "recover please");
  await shadowClick(page, ".send");

  await expect.poll(() => shadowText(page, ".messages")).toContain("Recovered: recover please");
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("agent-chat:http://127.0.0.1:8123")))
    .toBe("conv-fresh");
  expect(calls).toContain("GET /conversations/conv-stale/messages");
  expect(calls).toContain("POST /conversations");
  expect(calls).toContain("POST /conversations/conv-fresh/messages/stream");
});

test("script-only auto-mount creates one configured widget", async ({ page }) => {
  await mockConversationApi(page);
  await page.goto("/widget-demo-automount.html");

  await expect(page.locator("agent-chat")).toHaveCount(1);
  await shadowClick(page, ".launcher");
  await expect.poll(() => shadowText(page, ".title")).toBe("Auto-mounted Assistant");
  await expect.poll(() => shadowText(page, ".messages")).toContain("Hi from auto-mount");
});

test("auto-mount does not duplicate an authored element and attributes control the widget", async ({ page }) => {
  await mockConversationApi(page);
  await page.goto("/widget-demo-attribute-override.html");

  await expect(page.locator("agent-chat")).toHaveCount(1);
  await shadowClick(page, ".launcher");
  await expect.poll(() => shadowText(page, ".title")).toBe("Attribute Assistant");
  await expect.poll(() => shadowText(page, ".messages")).toContain("The attribute greeting wins.");
  await expect.poll(() => shadowClassContains(page, ".panel", "open")).toBe(true);
});
