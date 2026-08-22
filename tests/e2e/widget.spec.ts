import { expect, test, type Page, type Route } from "@playwright/test";

const history: Record<string, Array<{ role: string; content: string; created_at: string }>> = {};

const ENDPOINT = "http://127.0.0.1:8123";
const CONVERSATION_KEY = `agent-chat:${ENDPOINT}`;
const PASS_KEY = `agent-chat:pass:${ENDPOINT}`;

// The widget has no identity until the manager issues it a visitor pass. Seed
// one so tests exercise the chat rather than the pass hand-out.
async function pinVisitorPass(page: Page) {
  await page.addInitScript(
    ([key, pass]) => localStorage.setItem(key, pass),
    [PASS_KEY, "e2e-visitor-pass"],
  );
}

async function mockConversationApi(
  page: Page,
  options: {
    failSend?: boolean;
    threads?: Array<{ conversation_id: string; title: string | null; last_message_at: string | null }>;
    usedTools?: Array<{ name: string; provider: string; status: string }>;
  } = {},
) {
  const calls: string[] = [];
  await pinVisitorPass(page);

  await page.route(/\/conversations\?/, async (route) => {
    calls.push(`GET ${new URL(route.request().url()).pathname}`);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(options.threads ?? []),
    });
  });

  await page.route("**/conversations", async (route) => {
    const method = route.request().method();
    calls.push(`${method} ${new URL(route.request().url()).pathname}`);
    expect(route.request().headers()["authorization"]).toBe("Bearer e2e-visitor-pass");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body:
        method === "GET"
          ? JSON.stringify(options.threads ?? [])
          : JSON.stringify({ conversation_id: "conv-smoke", session_id: "conv-smoke" }),
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
        `event: final\ndata: ${JSON.stringify({ type: "final", content: `Echo: ${body.message || ""}`, route: [], used_tools: options.usedTools ?? [] })}`,
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

async function mockApprovalApi(
  page: Page,
  options: {
    decisionStatus?: number;
    decisionDetail?: string;
    failDecision?: boolean;
    decisionDelayMs?: number;
    blockResume?: boolean;
  } = {},
) {
  const decisions: string[] = [];
  const cancellations: string[] = [];
  const editTargets: Array<string | null> = [];
  let streamCount = 0;
  let sessionApproved = false;
  let cancelled = false;

  await page.route("**/conversations", async (route) => {
    const body =
      route.request().method() === "GET"
        ? []
        : { conversation_id: "conv-approval", session_id: "conv-approval" };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.route("**/conversations/*/messages/stream", async (route: Route) => {
    streamCount += 1;
    const request = JSON.parse(route.request().postData() || "{}") as {
      edit_message_id?: string;
    };
    editTargets.push(request.edit_message_id ?? null);
    const runId = `run-${streamCount}`;
    const messageId = `user-${streamCount}`;
    if (sessionApproved) {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: [
          `event: turn_started\ndata: ${JSON.stringify({ type: "turn_started", run_id: runId, message_id: messageId })}`,
          `event: final\ndata: ${JSON.stringify({ type: "final", content: "Session approval reused", route: ["writer"], used_tools: [] })}`,
          "event: done\ndata: [DONE]",
          "",
        ].join("\n\n"),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: [
        `event: turn_started\ndata: ${JSON.stringify({ type: "turn_started", run_id: runId, message_id: messageId })}`,
        `event: pending_approval\ndata: ${JSON.stringify({
          type: "pending_approval",
          route: ["writer"],
          run_id: runId,
          approval_id: `approval-${streamCount}`,
          agent_id: "writer",
          tool_name: "send_email",
          description: "Send an email to the selected recipient. This action has not been executed.",
          provider: "local",
          used_tools: [],
        })}`,
        "event: done\ndata: [DONE]",
        "",
      ].join("\n\n"),
    });
  });

  if (!options.blockResume) {
    await page.route(/\/conversations\/[^/]+\/runs\/[^/]+\/approvals\/[^/]+\/decision\/stream$/, async (route) => {
      const body = JSON.parse(route.request().postData() || "{}") as { decision?: string };
      decisions.push(body.decision || "");
      await new Promise((resolve) => setTimeout(resolve, options.decisionDelayMs ?? 25));
      if (cancelled) {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ detail: "approval already processed" }),
        });
        return;
      }
      sessionApproved ||= body.decision === "allow_for_session";
      if (options.failDecision || options.decisionStatus) {
        await route.fulfill({
          status: options.decisionStatus ?? 500,
          contentType: "application/json",
          body: JSON.stringify({
            detail: options.decisionDetail ?? "private approval failure",
          }),
        });
        return;
      }
      const answer =
        body.decision === "deny" ? "The tool request was denied." : "The tool completed.";
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: [
          `event: resume_started\ndata: ${JSON.stringify({ type: "resume_started", run_id: `run-${streamCount}` })}`,
          `event: final\ndata: ${JSON.stringify({ type: "final", content: answer, route: ["writer"], used_tools: [] })}`,
          "event: done\ndata: [DONE]",
          "",
        ].join("\n\n"),
      });
    });
  }

  await page.route(/\/conversations\/[^/]+\/runs\/[^/]+\/approvals\/[^/]+\/cancel$/, async (route) => {
    const path = new URL(route.request().url()).pathname;
    cancellations.push(path);
    cancelled = true;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ run_id: `run-${streamCount}`, status: "cancelled" }),
    });
  });

  await page.route("**/conversations/*/messages", async (route: Route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });

  await page.route("**/conversations/*/usage", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ used_tokens: 0, max_tokens: null, percent: 0, severity: "normal" }),
    });
  });

  return { decisions, cancellations, editTargets, getStreamCount: () => streamCount };
}

async function mockConversationApiWithStaleConversation(page: Page, staleStatus = 404) {
  await pinVisitorPass(page);
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
        status: staleStatus,
        contentType: "application/json",
        body: JSON.stringify({ detail: "unavailable" }),
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
        status: staleStatus,
        contentType: "application/json",
        body: JSON.stringify({ detail: "unavailable" }),
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

async function shadowClickText(page: Page, selector: string, text: string, index = 0) {
  const handle = await widget(page, index);
  await handle.evaluate(
    (element, { selector, text }) => {
      const targets = Array.from(element.shadowRoot?.querySelectorAll<HTMLElement>(selector) ?? []);
      targets.find((node) => node.textContent?.includes(text))?.click();
    },
    { selector, text },
  );
}

async function shadowClickUserAction(page: Page, text: string, label: string) {
  const handle = await widget(page);
  await handle.evaluate(
    (element, { text, label }) => {
      const messages = Array.from(element.shadowRoot?.querySelectorAll<HTMLElement>(".msg.user") ?? []);
      const message = messages.find((node) => node.querySelector(".message-content")?.textContent === text);
      message?.querySelector<HTMLElement>(`[aria-label="${label}"]`)?.click();
    },
    { text, label },
  );
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
  await pinVisitorPass(page);
  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "hello browser");
  await page.keyboard.press("Enter");

  await expect.poll(() => shadowText(page, ".messages")).toContain("Echo: hello browser");
  await expect.poll(() => shadowActiveMatches(page, ".input")).toBe(true);
  await expect
    .poll(() => page.evaluate((key) => localStorage.getItem(key), CONVERSATION_KEY))
    .toBe("conv-smoke");
  expect(calls).toContain("POST /conversations");
  expect(calls).toContain("POST /conversations/conv-smoke/messages/stream");

  await page.reload();
  await shadowClick(page, ".launcher");
  await expect.poll(() => shadowText(page, ".messages")).toContain("hello browser");
  await expect.poll(() => shadowText(page, ".messages")).toContain("Echo: hello browser");
  expect(calls).toContain("GET /conversations/conv-smoke/messages");
});

test("Stop cancels the active stream while preserving the next draft", async ({ page }) => {
  const calls = await mockConversationApi(page);
  let streamCount = 0;
  let releaseFirst!: () => void;
  const firstReleased = new Promise<void>((resolve) => {
    releaseFirst = resolve;
  });

  await page.route("**/conversations/conv-smoke/messages/stream", async (route: Route) => {
    streamCount += 1;
    if (streamCount === 1) {
      await firstReleased;
      try {
        await route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: [
            `event: final\ndata: ${JSON.stringify({ type: "final", content: "late answer", route: [], used_tools: [] })}`,
            "event: done\ndata: [DONE]",
            "",
          ].join("\n\n"),
        });
      } catch {
        // The browser already abandoned the intercepted request.
      }
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: [
        `event: final\ndata: ${JSON.stringify({ type: "final", content: "next answer", route: [], used_tools: [] })}`,
        "event: done\ndata: [DONE]",
        "",
      ].join("\n\n"),
    });
  });

  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "first prompt");
  await shadowClick(page, ".send");

  await expect.poll(() => streamCount).toBe(1);
  await expect.poll(() => shadowAttribute(page, ".send", "aria-label")).toBe("Stop generating");
  await expect.poll(() => shadowExists(page, ".input:disabled")).toBe(false);

  await shadowFill(page, ".input", "next draft");
  await page.keyboard.press("Enter");
  expect(streamCount).toBe(1);
  await expect.poll(() => shadowValue(page, ".input")).toBe("next draft");

  await shadowClick(page, ".send");
  await expect.poll(() => shadowAttribute(page, ".send", "aria-label")).toBe("Send message");
  await expect.poll(() => shadowValue(page, ".input")).toBe("next draft");
  await expect.poll(() => shadowExists(page, ".thinking")).toBe(false);
  await expect.poll(() => shadowText(page, ".msg-cancelled")).toBe("Generation stopped");
  expect(await shadowText(page, ".messages")).not.toContain("Something went wrong");
  expect(calls).not.toContain("POST /conversations/conv-smoke/messages");

  releaseFirst();
  await page.waitForTimeout(50);
  expect(await shadowText(page, ".messages")).not.toContain("late answer");

  await shadowClick(page, ".send");
  await expect.poll(() => streamCount).toBe(2);
  await expect.poll(() => shadowText(page, ".messages")).toContain("next answer");
});

test("editing a user message creates a new visible branch and can be cancelled", async ({ page }) => {
  await mockConversationApi(page);
  await page.addInitScript(
    ([key, value]) => localStorage.setItem(key, value),
    [CONVERSATION_KEY, "conv-edit"],
  );
  const original = [
    { message_id: "u1", run_id: "r1", role: "user", content: "U1", status: "completed", created_at: "2026-01-01T00:00:00Z" },
    { message_id: "a1", run_id: "r1", role: "assistant", content: "A1", status: "completed", created_at: "2026-01-01T00:00:01Z" },
    { message_id: "u2", run_id: "r2", role: "user", content: "U2", status: "completed", created_at: "2026-01-01T00:00:02Z" },
    { message_id: "a2", run_id: "r2", role: "assistant", content: "A2", status: "completed", created_at: "2026-01-01T00:00:03Z" },
  ];
  let active = [...original];
  let editTarget = "";
  await page.route("**/conversations/conv-edit/messages", async (route: Route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(active) });
  });
  await page.route("**/conversations/conv-edit/messages/stream", async (route: Route) => {
    const body = JSON.parse(route.request().postData() || "{}") as {
      message: string;
      edit_message_id?: string;
    };
    editTarget = body.edit_message_id || "";
    active = [
      ...original.slice(0, 2),
      { message_id: "u2-edited", run_id: "r3", role: "user", content: body.message, status: "completed", created_at: "2026-01-01T00:00:04Z" },
      { message_id: "a2-edited", run_id: "r3", role: "assistant", content: "A2 edited", status: "completed", created_at: "2026-01-01T00:00:05Z" },
    ];
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: [
        `event: turn_started\ndata: ${JSON.stringify({ type: "turn_started", run_id: "r3", message_id: "u2-edited" })}`,
        `event: final\ndata: ${JSON.stringify({ type: "final", content: "A2 edited", route: [], used_tools: [] })}`,
        "event: done\ndata: [DONE]",
        "",
      ].join("\n\n"),
    });
  });

  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await expect.poll(() => shadowText(page, ".messages")).toContain("A2");
  await expect.poll(() => shadowComputedStyle(page, ".user-actions", "opacity")).toBe("1");
  await shadowFill(page, ".input", "draft in progress");

  await shadowClickUserAction(page, "U2", "Edit message");
  await expect.poll(() => shadowValue(page, ".input")).toBe("U2");
  await shadowClick(page, ".edit-cancel");
  await expect.poll(() => shadowValue(page, ".input")).toBe("draft in progress");
  expect(await shadowText(page, ".messages")).toContain("A2");

  await shadowClickUserAction(page, "U2", "Edit message");
  await shadowFill(page, ".input", "U2 edited");
  await shadowClick(page, ".send");

  await expect.poll(() => editTarget).toBe("u2");
  await expect.poll(() => shadowText(page, ".messages")).toContain("U2 edited");
  await expect.poll(() => shadowText(page, ".messages")).toContain("A2 edited");
  expect(await shadowText(page, ".messages")).not.toContain("A2A2 edited");
  expect(original.map((message) => message.content)).toEqual(["U1", "A1", "U2", "A2"]);
});

test("cancelled history renders lifecycle state without persisted assistant text", async ({ page }) => {
  await mockConversationApi(page);
  await page.addInitScript(
    ([key, value]) => localStorage.setItem(key, value),
    [CONVERSATION_KEY, "conv-cancelled"],
  );
  await page.route("**/conversations/conv-cancelled/messages", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          message_id: "cancelled-user",
          run_id: "cancelled-run",
          role: "user",
          content: "Explain RocksDB",
          status: "cancelled",
          created_at: "2026-01-01T00:00:00Z",
        },
      ]),
    });
  });

  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await expect.poll(() => shadowText(page, ".msg-cancelled")).toBe("Generation stopped");
  await expect.poll(() => shadowText(page, ".messages")).toContain("Explain RocksDB");
  await expect.poll(() => shadowExists(page, '[aria-label="Edit message"]')).toBe(true);
  await expect.poll(() => shadowComputedStyle(page, ".user-actions", "opacity")).toBe("1");
});

test("tool activity shows the tool name without its internal provider", async ({ page }) => {
  await mockConversationApi(page, {
    usedTools: [{ name: "search_docs", provider: "mcp", status: "succeeded" }],
  });
  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "find the docs");
  await page.keyboard.press("Enter");

  await expect.poll(() => shadowText(page, ".messages")).toContain("Echo: find the docs");
  await expect.poll(() => shadowText(page, ".tool-title")).toBe("search_docs");
  await expect.poll(() => shadowText(page, ".tool-title")).not.toContain("mcp");
});

for (const action of [
  { label: "Approve", decision: "allow_once", answer: "The tool completed." },
  { label: "Deny", decision: "deny", answer: "The tool request was denied." },
  {
    label: "Approve for this session",
    decision: "allow_for_session",
    answer: "The tool completed.",
  },
]) {
  test(`${action.label} resolves a pending tool request exactly once`, async ({ page }) => {
    const approval = await mockApprovalApi(page);
    await page.goto("/widget-demo.html");
    await shadowClick(page, ".launcher");
    await shadowFill(page, ".input", "send the email");
    await shadowClick(page, ".send");

    await expect.poll(() => shadowText(page, ".approval-card")).toContain("Approval required");
    await expect
      .poll(() => shadowText(page, ".approval-description"))
      .toBe("Send an email to the selected recipient. This action has not been executed.");
    await expect.poll(() => shadowText(page, ".approval-tool")).toBe("Tool: send_email");
    expect(await shadowText(page, ".approval-card")).not.toContain("writer");
    expect(await shadowText(page, ".approval-card")).not.toContain("mail-prod");
    expect(await shadowText(page, ".approval-card")).not.toContain("provider");
    await expect.poll(() => shadowText(page, ".approval-actions")).toContain("Approve");
    await expect.poll(() => shadowText(page, ".approval-actions")).toContain("Deny");
    await expect
      .poll(() => shadowText(page, ".approval-actions"))
      .toContain("Approve for this session");
    await expect.poll(() => shadowExists(page, ".input:disabled")).toBe(true);

    await shadowClickText(page, ".approval-button", action.label);
    await shadowClickText(page, ".approval-button", action.label);

    await expect.poll(() => approval.decisions).toEqual([action.decision]);
    await expect.poll(() => shadowExists(page, ".approval-card")).toBe(false);
    await expect.poll(() => shadowText(page, ".messages")).toContain(action.answer);
    await expect.poll(() => shadowExists(page, ".input:disabled")).toBe(false);
    await expect.poll(() => shadowExists(page, ".approval-status")).toBe(false);
  });
}

test("a pending approval can be cancelled even while a decision is applying", async ({ page }) => {
  const approval = await mockApprovalApi(page, { decisionDelayMs: 500 });
  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "send the email");
  await shadowClick(page, ".send");

  await expect.poll(() => shadowText(page, ".approval-actions")).toContain("Cancel run");
  await shadowClickText(page, ".approval-button", "Approve");
  await expect.poll(() => shadowText(page, ".approval-status")).toBe("Applying decision…");
  await shadowClickText(page, ".approval-button", "Cancel run");

  await expect.poll(() => approval.cancellations).toHaveLength(1);
  await expect.poll(() => shadowExists(page, ".approval-card")).toBe(false);
  await expect.poll(() => shadowText(page, ".msg-cancelled")).toBe("Generation stopped");
  await expect.poll(() => shadowExists(page, ".input:disabled")).toBe(false);
  expect(approval.decisions).toEqual(["allow_once"]);
});

test("resumed approval keeps draft and Edit available and Stop cancels it", async ({ page }) => {
  const approval = await mockApprovalApi(page, { blockResume: true });
  const nonStreamingDecisions: string[] = [];
  page.on("request", (request) => {
    if (/\/approvals\/[^/]+\/decision$/.test(new URL(request.url()).pathname)) {
      nonStreamingDecisions.push(request.url());
    }
  });
  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "send the email");
  await shadowClick(page, ".send");
  await expect.poll(() => shadowExists(page, ".approval-card")).toBe(true);

  await shadowClickText(page, ".approval-button", "Approve");
  await expect.poll(() => shadowAttribute(page, ".send", "aria-label")).toBe("Stop generating");
  await expect.poll(() => shadowExists(page, ".approval-card")).toBe(false);
  await expect.poll(() => shadowExists(page, ".input:disabled")).toBe(false);
  await expect.poll(() => shadowExists(page, '[aria-label="Edit message"]')).toBe(true);

  await shadowFill(page, ".input", "next prompt draft");
  await shadowClickUserAction(page, "send the email", "Edit message");
  await shadowFill(page, ".input", "edited prompt draft");
  await page.keyboard.press("Enter");
  await expect.poll(() => shadowValue(page, ".input")).toBe("edited prompt draft");
  expect(approval.getStreamCount()).toBe(1);

  await shadowClick(page, ".send");
  await expect.poll(() => shadowText(page, ".msg-cancelled")).toBe("Generation stopped");
  await expect.poll(() => shadowAttribute(page, ".send", "aria-label")).toBe("Send message");
  await expect.poll(() => shadowValue(page, ".input")).toBe("edited prompt draft");
  expect(await shadowText(page, ".messages")).not.toContain("Something went wrong");
  expect(nonStreamingDecisions).toEqual([]);
});

test("session approval prevents another prompt for the same session tool", async ({ page }) => {
  const approval = await mockApprovalApi(page);
  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "first email");
  await shadowClick(page, ".send");
  await expect.poll(() => shadowExists(page, ".approval-card")).toBe(true);

  await shadowClickText(page, ".approval-button", "Approve for this session");
  await expect.poll(() => shadowExists(page, ".approval-card")).toBe(false);

  await shadowFill(page, ".input", "second email");
  await shadowClick(page, ".send");

  await expect.poll(() => shadowText(page, ".messages")).toContain("Session approval reused");
  await expect.poll(() => approval.getStreamCount()).toBe(2);
  expect(approval.decisions).toEqual(["allow_for_session"]);
  await expect.poll(() => shadowExists(page, ".approval-card")).toBe(false);
});

test("editing a pending-approval prompt creates a branch without resuming it", async ({ page }) => {
  const approval = await mockApprovalApi(page);
  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "original request");
  await shadowClick(page, ".send");
  await expect.poll(() => shadowExists(page, ".approval-card")).toBe(true);

  await shadowClickUserAction(page, "original request", "Edit message");
  await expect.poll(() => shadowExists(page, ".input:disabled")).toBe(false);
  await shadowFill(page, ".input", "revised request");
  await shadowClick(page, ".send");

  await expect.poll(() => approval.getStreamCount()).toBe(2);
  expect(approval.editTargets).toEqual([null, "user-1"]);
  expect(approval.decisions).toEqual([]);
  await expect.poll(() => shadowText(page, ".messages")).toContain("revised request");
  await expect.poll(() => shadowExists(page, ".approval-card")).toBe(true);
});

test("a failed approval decision restores the controls without leaking details", async ({ page }) => {
  const approval = await mockApprovalApi(page, { decisionStatus: 500 });
  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "send the email");
  await shadowClick(page, ".send");
  await expect.poll(() => shadowExists(page, ".approval-card")).toBe(true);

  await shadowClickText(page, ".approval-button", "Approve");

  await expect.poll(() => approval.decisions).toEqual(["allow_once"]);
  await expect.poll(() => shadowExists(page, ".approval-card")).toBe(true);
  await expect.poll(() => shadowText(page, ".approval-error")).toBe(
    "Something went wrong. Please try again.",
  );
  await expect.poll(() => shadowExists(page, ".approval-status")).toBe(false);
  await expect.poll(() => shadowExists(page, ".approval-button:disabled")).toBe(false);
  await expect.poll(() => shadowExists(page, ".input:disabled")).toBe(true);
  expect(await shadowText(page, ".approval-card")).not.toContain("private approval failure");
});

test("a terminal approval failure releases the composer and leaves a safe error", async ({
  page,
}) => {
  const approval = await mockApprovalApi(page, {
    decisionStatus: 404,
    decisionDetail: "approval not found",
  });
  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "send the email");
  await shadowClick(page, ".send");
  await expect.poll(() => shadowExists(page, ".approval-card")).toBe(true);

  await shadowClickText(page, ".approval-button", "Approve");

  await expect.poll(() => approval.decisions).toEqual(["allow_once"]);
  await expect.poll(() => shadowExists(page, ".approval-card")).toBe(false);
  await expect
    .poll(() => shadowText(page, ".msg-error"))
    .toBe("This approval is no longer available. You can continue chatting.");
  await expect.poll(() => shadowExists(page, ".input:disabled")).toBe(false);
  await shadowFill(page, ".input", "continue chatting");
  await expect.poll(() => shadowValue(page, ".input")).toBe("continue chatting");
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
  const calls = await mockConversationApi(page, { failSend: true });
  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "please fail");
  await shadowClick(page, ".send");

  await expect.poll(() => shadowText(page, ".messages")).toContain("Something went wrong. Please try again.");
  expect(calls).not.toContain("POST /conversations/conv-smoke/messages");
});

test("budget meter shows cumulative token usage against the budget after a turn", async ({ page }) => {
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
  await expect.poll(() => shadowExists(page, ".budget-meter")).toBe(true);
  await expect.poll(() => shadowText(page, ".budget-percent")).toBe("90%");
  await expect.poll(() => shadowClassContains(page, ".budget-meter", "critical")).toBe(true);
});

test("budget meter stays hidden when no budget is configured", async ({ page }) => {
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
  await expect.poll(() => shadowExists(page, ".budget-meter")).toBe(false);
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

test("thread drawer keeps conversation rows readable and scrollable", async ({ page }) => {
  const threads = Array.from({ length: 30 }, (_, index) => ({
    conversation_id: `conv-${index}`,
    title: `Conversation ${index}`,
    last_message_at: "2026-06-01T00:00:00Z",
  }));
  await mockConversationApi(page, { threads });

  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await shadowClick(page, '.header-btn[aria-label="Conversations"]');

  const handle = await widget(page);
  const metrics = await handle.evaluate((element) => {
    const root = element.shadowRoot;
    const list = root?.querySelector<HTMLElement>(".thread-list");
    const items = Array.from(root?.querySelectorAll<HTMLElement>(".thread-item") ?? []);
    if (!list || items.length === 0) throw new Error("Thread drawer did not render");

    return {
      itemCount: items.length,
      itemHeights: items.map((item) => item.getBoundingClientRect().height),
      itemFlexShrink: getComputedStyle(items[0]).flexShrink,
      listClientHeight: list.clientHeight,
      listScrollHeight: list.scrollHeight,
    };
  });

  expect(metrics.itemCount).toBe(30);
  expect(metrics.itemFlexShrink).toBe("0");
  expect(Math.min(...metrics.itemHeights)).toBeGreaterThan(30);
  expect(metrics.listScrollHeight).toBeGreaterThan(metrics.listClientHeight);
});

test("thinking dots persist when switching away from an in-flight thread and back", async ({
  page,
}) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });

  await mockConversationApi(page, {
    threads: [
      { conversation_id: "conv-other", title: "Other chat", last_message_at: "2026-06-01T00:00:00Z" },
      { conversation_id: "conv-smoke", title: "Current chat", last_message_at: "2026-06-28T00:00:00Z" },
    ],
  });
  await page.route("**/conversations/conv-other/messages", async (route: Route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/conversations/conv-smoke/messages/stream", async (route: Route) => {
    await pending;
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: [
        `event: final\ndata: ${JSON.stringify({ type: "final", content: "done", route: [], used_tools: [] })}`,
        "event: done\ndata: [DONE]",
        "",
      ].join("\n\n"),
    });
  });

  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "hello");
  await shadowClick(page, ".send");

  await expect.poll(() => shadowExists(page, ".thinking")).toBe(true);

  await shadowClick(page, '.header-btn[aria-label="Conversations"]');
  await shadowClickText(page, ".thread-item", "Other chat");
  await expect.poll(() => shadowExists(page, ".thinking")).toBe(false);

  await shadowClick(page, '.header-btn[aria-label="Conversations"]');
  await shadowClickText(page, ".thread-item", "Current chat");
  await expect.poll(() => shadowExists(page, ".thinking")).toBe(true);

  release();
  await expect.poll(() => shadowText(page, ".messages")).toContain("done");
  await expect.poll(() => shadowExists(page, ".thinking")).toBe(false);
});

test("a failed stream is not replayed after switching threads", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  const usageCalls: string[] = [];

  const calls = await mockConversationApi(page, {
    threads: [
      { conversation_id: "conv-other", title: "Other chat", last_message_at: "2026-06-01T00:00:00Z" },
      { conversation_id: "conv-smoke", title: "Current chat", last_message_at: "2026-06-28T00:00:00Z" },
    ],
  });
  await page.route("**/conversations/*/usage", async (route: Route) => {
    usageCalls.push(new URL(route.request().url()).pathname);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ used_tokens: 0, max_tokens: null, percent: 0, severity: "normal" }),
    });
  });
  // Hold the stream open until the user has switched away, then fail it. The
  // mutation must not be replayed because delivery failure is ambiguous.
  await page.route("**/conversations/conv-smoke/messages/stream", async (route: Route) => {
    await pending;
    await route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "stream unavailable" }),
    });
  });
  await page.route("**/conversations/conv-other/messages", async (route: Route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });

  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "hello");
  await shadowClick(page, ".send");
  await expect.poll(() => shadowExists(page, ".thinking")).toBe(true);

  await shadowClick(page, '.header-btn[aria-label="Conversations"]');
  await shadowClickText(page, ".thread-item", "Other chat");
  await expect.poll(() => shadowExists(page, ".thinking")).toBe(false);

  release();

  await expect
    .poll(() => usageCalls[usageCalls.length - 1])
    .toBe("/conversations/conv-smoke/usage");
  expect(calls).not.toContain("POST /conversations/conv-smoke/messages");
  expect(calls).not.toContain("POST /conversations/conv-other/messages");

  await shadowClick(page, '.header-btn[aria-label="Conversations"]');
  await shadowClickText(page, ".thread-item", "Current chat");
  await expect.poll(() => shadowText(page, ".messages")).toContain(
    "Something went wrong. Please try again.",
  );
});

async function mockConversationApiWithTokenBudgetExceeded(page: Page) {
  const calls: string[] = [];

  await page.route("**/conversations", async (route) => {
    calls.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ conversation_id: "conv-budget", session_id: "conv-budget" }),
    });
  });

  await page.route("**/conversations/*/messages/stream", async (route: Route) => {
    calls.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
    await route.fulfill({
      status: 429,
      contentType: "application/json",
      body: JSON.stringify({
        detail: {
          error_type: "context_limit_exceeded",
          message: "This conversation has reached its context limit. Start a new chat to continue.",
        },
      }),
    });
  });

  await page.route("**/conversations/*/messages", async (route: Route) => {
    calls.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
      return;
    }
    await route.fulfill({
      status: 429,
      contentType: "application/json",
      body: JSON.stringify({
        detail: {
          error_type: "context_limit_exceeded",
          message: "This conversation has reached its context limit. Start a new chat to continue.",
        },
      }),
    });
  });

  return calls;
}

test("token budget exceeded shows context-limit message instead of generic error", async ({
  page,
}) => {
  const calls = await mockConversationApiWithTokenBudgetExceeded(page);
  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "one more message");
  await shadowClick(page, ".send");

  await expect
    .poll(() => shadowText(page, ".messages"))
    .toContain("This conversation has reached its context limit. Start a new chat to continue.");
  const text = await shadowText(page, ".messages");
  expect(text).not.toContain("Something went wrong. Please try again.");
  expect(calls).toContain("POST /conversations/conv-budget/messages/stream");
  expect(calls).not.toContain("POST /conversations/conv-budget/messages");

  const inputEl = page.locator("agent-chat");
  const isDisabled = await inputEl.evaluate(
    (el) => !!el.shadowRoot?.querySelector(".input")?.hasAttribute("disabled"),
  );
  expect(isDisabled).toBe(true);

  const placeholder = await shadowAttribute(page, ".input", "placeholder");
  expect(placeholder).toBe("Context limit reached.");
});

test("a stored conversation owned by another caller is replaced, not retried forever", async ({
  page,
}) => {
  // What a host app switching users looks like: the stored id outlives the
  // identity that created it, so the server answers 403 and the widget has to
  // start over rather than sit on an id it can never use.
  const calls = await mockConversationApiWithStaleConversation(page, 403);
  await pinVisitorPass(page);
  await page.goto("/widget-demo.html");
  await page.evaluate((key) => localStorage.setItem(key, "conv-stale"), CONVERSATION_KEY);

  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "recover please");
  await shadowClick(page, ".send");

  await expect.poll(() => shadowText(page, ".messages")).toContain("Recovered: recover please");
  await expect
    .poll(() => page.evaluate((key) => localStorage.getItem(key), CONVERSATION_KEY))
    .toBe("conv-fresh");
  expect(calls).toContain("POST /conversations/conv-fresh/messages/stream");
});

test("stale stored conversation is replaced before sending to the agent", async ({ page }) => {

  const calls = await mockConversationApiWithStaleConversation(page);
  await pinVisitorPass(page);
  await page.goto("/widget-demo.html");
  await page.evaluate((key) => localStorage.setItem(key, "conv-stale"), CONVERSATION_KEY);

  await shadowClick(page, ".launcher");
  await shadowFill(page, ".input", "recover please");
  await shadowClick(page, ".send");

  await expect.poll(() => shadowText(page, ".messages")).toContain("Recovered: recover please");
  await expect
    .poll(() => page.evaluate((key) => localStorage.getItem(key), CONVERSATION_KEY))
    .toBe("conv-fresh");
  expect(calls).toContain("GET /conversations/conv-stale/messages");
  expect(calls).toContain("POST /conversations");
  expect(calls).toContain("POST /conversations/conv-fresh/messages/stream");
});

test("editing after an in-memory restart starts a fresh root instead of reusing a stale message id", async ({
  page,
}) => {
  await pinVisitorPass(page);
  await page.addInitScript(
    ([key, value]) => localStorage.setItem(key, value),
    [CONVERSATION_KEY, "conv-stale-edit"],
  );
  let restarted = false;
  const streamBodies: Array<{ message?: string; edit_message_id?: string }> = [];
  const usageConversationIds: string[] = [];

  await page.route("**/conversations", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ conversation_id: "conv-fresh-edit", session_id: "conv-fresh-edit" }),
    });
  });
  await page.route("**/conversations/*/messages", async (route: Route) => {
    const conversationId = new URL(route.request().url()).pathname.split("/")[2];
    if (conversationId === "conv-stale-edit" && !restarted) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            message_id: "stale-user-message",
            run_id: "stale-run",
            role: "user",
            content: "original text",
            status: "completed",
            created_at: "2026-01-01T00:00:00Z",
          },
        ]),
      });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });
  await page.route("**/conversations/*/messages/stream", async (route: Route) => {
    const conversationId = new URL(route.request().url()).pathname.split("/")[2];
    const body = JSON.parse(route.request().postData() || "{}") as {
      message?: string;
      edit_message_id?: string;
    };
    streamBodies.push(body);
    if (conversationId === "conv-stale-edit") {
      await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
      return;
    }
    if (body.edit_message_id) {
      await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: [
        `event: turn_started\ndata: ${JSON.stringify({ type: "turn_started", run_id: "fresh-run", message_id: "fresh-user-message" })}`,
        `event: final\ndata: ${JSON.stringify({ type: "final", content: "Recovered edited answer", route: [], used_tools: [] })}`,
        "event: done\ndata: [DONE]",
        "",
      ].join("\n\n"),
    });
  });
  await page.route("**/conversations/*/usage", async (route: Route) => {
    usageConversationIds.push(new URL(route.request().url()).pathname.split("/")[2] || "");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ used_tokens: 0, max_tokens: null, percent: 0, severity: "normal" }),
    });
  });

  await page.goto("/widget-demo.html");
  await shadowClick(page, ".launcher");
  await expect.poll(() => shadowText(page, ".messages")).toContain("original text");
  restarted = true;

  await shadowClickUserAction(page, "original text", "Edit message");
  await shadowFill(page, ".input", "edited after restart");
  await shadowClick(page, ".send");

  await expect.poll(() => shadowText(page, ".messages")).toContain("Recovered edited answer");
  expect(streamBodies).toEqual([
    { message: "edited after restart", edit_message_id: "stale-user-message" },
    { message: "edited after restart" },
  ]);
  await expect.poll(() => usageConversationIds[usageConversationIds.length - 1]).toBe("conv-fresh-edit");
  await expect
    .poll(() => page.evaluate((key) => localStorage.getItem(key), CONVERSATION_KEY))
    .toBe("conv-fresh-edit");
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
