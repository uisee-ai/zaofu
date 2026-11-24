import { expect, test, type Page, type Route, type WebSocketRoute } from "@playwright/test";

import { readFileSync } from "node:fs";
import { installUiFixture } from "./ui-alignment.fixture";

test.skip(!process.env.ZF_WEB_TERMINAL_FIXTURE, "Requires isolated dashboard");
const api = "**/terminal-sessions";
const first = { session_id: "one", title: "First terminal", slot: "one", project_id: "default",
  provider: "codex", state: "active", generation: 1, agent_status: "idle", diagnostics: [] };
const second = { ...first, session_id: "two", title: "Review terminal", agent_status: "blocked" };
const projection = { enabled: true, backend: "herdr", allowed_providers: ["codex"], allow_takeover: true,
  capability: { available: true, version: "0.9.5", mouse: true }, sessions: [first, second] };

async function dashboard(page: Page) {
  await page.addInitScript(token => localStorage.setItem("zf.webActionToken", token), process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST!);
  await page.route("**/terminal-sessions/*/attachments", route => route.fulfill({json: {
    subprotocol: "zf-terminal-v1", ticket: "fixture-ticket",
  }}));
  await page.goto("/");
  const skip = page.getByTestId("welcome-skip");
  if (await skip.waitFor({state: "visible", timeout: 2000}).then(() => true, () => false)) await skip.click();
  await page.getByRole("button", {name: "Toggle Terminal"}).click();
  return page.getByRole("complementary", {name: "Coding Agent Terminal"});
}

function frame(socket: WebSocketRoute, text: string) {
  socket.send(JSON.stringify({type: "terminal.frame", full: true, seq: 1, encoding: "ansi", width: 120, height: 40}));
  socket.send(Buffer.from(text));
}

test("restored tabs attach on visit and waiting items revalidate their generation", async ({page}) => {
  let generation = 1;
  let firstStatus = "idle";
  await page.route(api, route => route.fulfill({json: {...projection, sessions: [
    {...first, agent_status: firstStatus}, {...second, generation},
  ]}}));
  const attached: string[] = [];
  await page.routeWebSocket("**/control", socket => {
    const id = new URL(socket.url()).pathname.split("/").at(-2)!;
    attached.push(id);
    frame(socket, "history-" + id);
  });
  const drawer = await dashboard(page);
  await expect(drawer.getByRole("tab")).toHaveCount(2);
  await expect(drawer.locator(".is-active .xterm-screen")).toContainText("history-one");
  expect(attached).toEqual(["one"]);
  const waiting = drawer.getByRole("button", {name: "1 needs input"});
  await expect(waiting).toHaveText("1 needs input");
  if (process.env.ZF_TERMINAL_SCREENSHOTS) {
    await drawer.locator(".web-terminal-toolbar").screenshot({path: process.env.ZF_TERMINAL_SCREENSHOTS + "/waiting-count.png"});
  }
  await waiting.click();
  const target = drawer.getByRole("menuitem", {name: second.title});
  await target.focus();
  generation++;
  await target.click();
  await expect(drawer.getByRole("status")).toContainText("Session changed");
  await page.keyboard.press("Escape");
  await waiting.click();
  await target.click();
  await expect(drawer.locator(".is-active .xterm-screen")).toContainText("history-two");
  await drawer.getByRole("tab", {name: /First terminal/}).click();
  await expect(drawer.locator(".is-active .xterm-screen")).toContainText("history-one");
  expect(attached).toEqual(["one", "two"]);
  firstStatus = "blocked";
  await drawer.getByRole("button", {name: "Refresh terminal sessions"}).click();
  await expect(drawer.getByRole("button", {name: "2 need input"})).toHaveText("2 need input");
});

test("older list success and failure cannot overwrite the latest title or error", async ({page}) => {
  let hold = false;
  const pending: Route[] = [];
  await page.route(api, async route => {
    if (hold) pending.push(route);
    else await route.fulfill({json: projection});
  });
  await page.routeWebSocket("**/control", socket => frame(socket, "ready"));
  const drawer = await dashboard(page);
  await expect(drawer.getByRole("tab").first()).toContainText(first.title);
  hold = true;
  const refresh = drawer.getByRole("button", {name: "Refresh terminal sessions"});
  for (let i = 0; i < 3; i++) {
    await refresh.click();
    await expect.poll(() => pending.length).toBe(i + 1);
  }
  await pending[2].fulfill({json: {...projection, sessions: [{...first, title: "Latest title"}]}});
  await expect(drawer.getByRole("tab")).toContainText("Latest title");
  await pending[1].fulfill({json: projection});
  await pending[0].fulfill({status: 503, json: {reason: "stale fetch error"}});
  await expect(drawer.getByRole("tab")).toContainText("Latest title");
  await expect(drawer.getByText("stale fetch error")).toHaveCount(0);
});

test("interrupted input is visible, retries back off, overload and revocation have explicit endpoints", async ({page}) => {
  await page.clock.install();
  await page.route(api, route => route.fulfill({json: projection}));
  const sockets: WebSocketRoute[] = [];
  const sent: string[] = [];
  await page.routeWebSocket("**/control", socket => {
    sockets.push(socket);
    socket.onMessage(raw => {
      const value = JSON.parse(raw.toString());
      if (value.type === "terminal.input") sent.push(value.text);
    });
  });
  const drawer = await dashboard(page);
  const view = drawer.locator(".is-active .web-terminal-view");
  await expect(view).toHaveAttribute("data-terminal-status", "waiting for full frame");
  await page.clock.pauseAt(new Date(Date.now() + 3000));
  for (const [i, delay] of [250, 500, 1000].entries()) {
    sockets[i].close({code: 1011});
    await expect(view).toHaveAttribute("data-terminal-status", /reconnecting/);
    await page.clock.runFor(delay - 1);
    expect(sockets.length).toBe(i + 1);
    await page.clock.runFor(1);
    await expect.poll(() => sockets.length).toBe(i + 2);
  }
  frame(sockets[3], "stable baseline");
  await expect(view).toHaveAttribute("data-terminal-status", "controlling");
  await page.clock.runFor(10_100);
  const input = view.locator("textarea.xterm-helper-textarea");
  await input.focus();
  await input.press("a");
  await expect.poll(() => sent).toEqual(["a"]);
  sockets[3].close({code: 1011});
  await expect(view).toContainText("Check recent input");
  await input.press("Enter");
  await expect(view).toContainText("Input was not sent");
  await input.evaluate(el => {
    const data = new DataTransfer();
    data.setData("text/plain", "断线时粘贴".repeat(4096));
    el.dispatchEvent(new ClipboardEvent("paste", {clipboardData: data, bubbles: true}));
  });
  await page.clock.runFor(250);
  await expect.poll(() => sockets.length).toBe(5);
  frame(sockets[4], "new baseline");
  await expect(view).toHaveAttribute("data-terminal-status", "controlling");
  expect(sent).toEqual(["a"]);
  sockets[4].close({code: 1013, reason: "terminal output overloaded"});
  await expect(view).toHaveAttribute("data-terminal-status", "overloaded");
  await page.clock.runFor(6000);
  expect(sockets.length).toBe(5);
  await view.getByRole("button", {name: "Reconnect"}).click();
  await expect.poll(() => sockets.length).toBe(6);
  await page.evaluate(() => Object.defineProperty(document, "hidden", {configurable: true, value: true}));
  sockets[5].close({code: 1013, reason: "terminal output overloaded"});
  await expect(view).toHaveAttribute("data-terminal-status", "overloaded");
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {configurable: true, value: false});
    document.dispatchEvent(new Event("visibilitychange"));
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(() => sockets.length).toBe(7);
  sockets[6].close({code: 4403, reason: "Access revoked"});
  await expect(view).toContainText("Access revoked");
  await expect(view).toHaveAttribute("data-terminal-status", "closed");
  await page.clock.runFor(6000);
  expect(sockets.length).toBe(7);
});

for (const destination of ["channel", "task"] as const) {
  test(`selected output opens an editable ${destination} draft only after choosing a destination`, async ({page}) => {
    const { writes } = await installUiFixture(page, {snapshot: {runtime: {mode: "live",
      actions: {mutation_enabled: true, allowed: ["chat-orchestrator", "channel-post-message"]},
      web_session: {mode: "local_trusted", actions_enabled: true},
    } }});
    const channel = {channel_id: "review", name: "Review room", members: [], messages: []};
    await page.route("**/channels", route => route.fulfill({json: {channels: [channel]}}));
    await page.route("**/channels/review/conversation?**", route => route.fulfill({json: channel}));
    await page.route(api, route => route.fulfill({json: projection}));
    const selected = "中文 ``` failure";
    await page.routeWebSocket("**/control", socket => frame(socket, selected));
    const drawer = await dashboard(page);
    const screen = drawer.locator(".is-active .xterm-screen");
    await expect(screen).toContainText(selected);
    const row = await screen.locator(".xterm-rows > div").first().boundingBox();
    await page.mouse.move(row!.x + 1, row!.y + row!.height / 2);
    await page.mouse.down();
    await page.mouse.move(row!.x + 200, row!.y + row!.height / 2, {steps: 10});
    await page.mouse.up();
    const quote = drawer.getByRole("button", {name: "Quote selection"});
    await quote.click();
    await expect(drawer.getByLabel("Quoted text")).toHaveValue(/First terminal \(one · g1\) · lines 1–1/);
    await expect(drawer.getByLabel("Quoted text")).toHaveValue(/`{4}\n中文 `{3} failure\n`{4}/);
    await drawer.getByRole("button", {name: "Cancel", exact: true}).click();
    expect(writes).toEqual([]);
    await quote.click();
    await drawer.getByLabel("Discussion destination").selectOption(destination === "task" ? "task:UI-101" : "channel:review");
    await drawer.getByLabel("Quoted text").fill("edited excerpt from the terminal");
    await drawer.getByRole("button", {name: "Open discussion draft"}).click();
    const editor = destination === "task" ? page.locator("textarea.headless-input") : page.locator(".channel-composer-input");
    if (destination === "task") await expect(editor).toHaveValue("edited excerpt from the terminal");
    else await expect(editor).toHaveText("edited excerpt from the terminal");
    expect(writes.filter(entry => /channel-post-message|chat-orchestrator/.test(entry.path))).toEqual([]);
    if (destination === "channel") {
      await page.getByRole("button", {name: "Send message"}).click();
      await expect.poll(() => writes.filter(entry => entry.path.endsWith("/actions/channel-post-message"))).toHaveLength(1);
      expect(JSON.stringify(writes.at(-1)?.body)).toContain("edited excerpt from the terminal");
    }
  });
}

test("a settled parent opens only its own child activity and source evidence", async ({page}) => {
  await installUiFixture(page);
  const thread = "kanban:ui-review";
  const binding = {project_id: "ui-review", conversation_id: thread, thread_key: thread,
    turn_id: "turn-review", backend: "codex-headless"};
  const events = [
    ["user.message", {target: "kanban-agent", runtime_delivery: "headless", message: "Check the release"}],
    ["kanban.agent.turn.created", {message_event_id: "child-fixture-1"}],
    ["kanban.agent.reply", {answer: "Parent has replied"}],
    ["kanban.agent.turn.completed", {status: "completed"}],
  ].map(([type, payload], i) => ({id: "child-fixture-" + (i + 1), seq: i + 1, ts: "2026-11-23T09:00:00Z",
    type, payload: {...binding, ...(payload as object)}}));
  await page.route("**/agent-session/history?**", route => route.fulfill({json: {items: events, has_more: false}}));
  await page.route("**/webchat/runtime?**", route => route.fulfill({json: {turns: [{...binding, thread_id: thread,
    state: "completed", current_attempt_id: "attempt-one"}], interactions: [], queue: [], threads: [], artifacts: [],
    messages: [], capabilities: {controls: [], actions: []}}}));
  const parents: string[] = [];
  await page.route("**/webchat/child-activities**", route => {
    parents.push(new URL(route.request().url()).searchParams.get("parent_turn_id") || "");
    return route.fulfill({json: {items: [
      {activity_id: "child-review", role: "Reviewer", provider: "codex", status: "active",
        why_not_done: "No host-observed terminal evidence."},
      {activity_id: "child-test", role: "Tester", provider: "codex", status: "terminal", terminal_evidence: [
        {event_id: "test-event", event_type: "worker.done", ref: "tests/receipt.json"}]},
    ]}});
  });
  await page.goto("/?project=ui-review");
  await page.getByRole("button", {name: "Open Kanban Agent"}).click();
  await expect(page.getByText("Parent has replied", {exact: true})).toBeVisible();
  await page.getByRole("button", {name: "View child activity"}).first().click();
  const detail = page.getByRole("dialog", {name: "Child activity"});
  await expect(detail).toContainText("No host-observed terminal evidence");
  await detail.getByRole("button", {name: /Tester/}).click();
  await detail.getByText("Terminal evidence", {exact: true}).click();
  await expect(detail).toContainText("tests/receipt.json");
  expect(parents.every(id => id === binding.turn_id)).toBe(true);
});

test("a frozen observer pauses at the parser limit while the controller can interrupt", async ({page, browser}) => {
  const baseline = "Shared terminal";
  await page.route(api, route => route.fulfill({json: projection}));
  const input: string[] = [];
  await page.routeWebSocket("**/control", socket => {
    frame(socket, baseline);
    socket.onMessage(raw => {
      const message = JSON.parse(raw.toString());
      if (message.type === "terminal.input") input.push(message.text);
    });
  });
  const control = await dashboard(page);
  await expect(control.locator(".is-active .xterm-screen")).toContainText(baseline);
  const observer = await browser.newPage({baseURL: process.env.ZF_WEB_BASE_URL});
  try {
    await observer.clock.install();
    await observer.route(api, route => route.fulfill({json: {...projection, allow_takeover: false}}));
    await observer.routeWebSocket("**/control", socket => socket.close({code: 1008}));
    const observers: WebSocketRoute[] = [];
    await observer.routeWebSocket("**/observe", socket => {
      observers.push(socket);
      frame(socket, baseline);
    });
    const drawer = await dashboard(observer);
    await drawer.getByRole("button", {name: "Terminal actions"}).click();
    await drawer.getByRole("menu").getByText("Observe", {exact: true}).click();
    await expect(drawer.locator(".is-active .xterm-screen")).toContainText(baseline);
    await expect(drawer.getByText("Read-only", {exact: true})).toBeVisible();
    await expect(drawer.getByRole("button", {name: "Take over control"})).toHaveCount(0);
    await observer.clock.pauseAt(new Date(Date.now() + 3000));
    const batch = Buffer.alloc(1024 * 1024);
    const report = observer.waitForEvent("console", msg => msg.text().startsWith("Terminal parser backlog"));
    for (let seq = 2; seq <= 10; seq++) {
      observers[0].send(JSON.stringify({type: "terminal.frame", full: false, seq, encoding: "ansi", width: 120, height: 40}));
      observers[0].send(batch);
    }
    const metrics = await (await report).args()[1].jsonValue();
    expect(metrics).toEqual({bytes: 9 * batch.length, receivedSeq: 10, parsedSeq: 1});
    await expect(drawer.getByRole("button", {name: "Reconnect"})).toBeVisible();
    await control.locator(".is-active textarea.xterm-helper-textarea").press("Control+c");
    await expect.poll(() => input).toContain("\x03");
    await drawer.getByRole("button", {name: "Reconnect"}).click();
    await expect.poll(() => observers.length).toBe(2);
    await observer.clock.runFor(1000);
    await expect(drawer.locator(".is-active .web-terminal-view")).toHaveAttribute("data-terminal-status", "observing");
    await expect(drawer.locator(".is-active .xterm-screen")).toContainText(baseline);
  } finally {
    await observer.close();
  }
});

test("emoji clusters line up with the Herdr cell targeted by a click", async ({page}) => {
  await page.route(api, route => route.fulfill({json: projection}));
  const clicks: Array<{row: number; column: number}> = [];
  await page.routeWebSocket("**/control", socket => {
    frame(socket, "a中⚠️👨‍👩‍👧👍🏽\x1b[31mX\x1b[0m");
    socket.onMessage(raw => {
      const value = JSON.parse(raw.toString());
      if (value.type === "terminal.mouse" && value.action === "down") clicks.push({row: value.row, column: value.column});
    });
  });
  const drawer = await dashboard(page);
  const marker = drawer.locator(".is-active .xterm-screen").getByText("X", {exact: true});
  await expect(marker).toBeVisible();
  const cell = (await marker.boundingBox())!;
  await page.mouse.click(cell.x + cell.width / 2, cell.y + cell.height / 2);
  await expect.poll(() => clicks).toEqual([{row: 0, column: 9}]);
});

test("scrolling away and reattaching repaints captured Codex history and menu cells", async ({page}) => {
  const capture = JSON.parse(readFileSync(new URL("./fixtures/herdr-codex-prompt.json", import.meta.url), "utf8"));
  const prompt = capture.ansi_rows.join("\r\n");
  await page.emulateMedia({colorScheme: "light"});
  await page.addInitScript(() => localStorage.setItem("zf.themeMode", "system"));
  await page.route(api, route => route.fulfill({json: projection}));
  let scrolls = 0;
  await page.routeWebSocket("**/control", socket => {
    frame(socket, prompt);
    socket.onMessage(raw => {
      if (JSON.parse(raw.toString()).type !== "terminal.scroll") return;
      scrolls++;
      socket.send(JSON.stringify({type: "terminal.frame", full: true, seq: 2, encoding: "ansi", width: 120, height: 40}));
      socket.send(Buffer.from("Earlier output"));
    });
  });
  await page.routeWebSocket("**/observe", socket => frame(socket, prompt));
  const drawer = await dashboard(page);
  const screen = drawer.locator(".is-active .xterm-screen");
  const marker = screen.locator(".web-terminal-codex-prompt").filter({hasText: "请检查界面排版"});
  await expect(marker).toBeVisible();
  await screen.hover();
  await page.mouse.wheel(0, -640);
  await expect(screen).toContainText("Earlier output");
  await expect(marker).toHaveCount(0);
  expect(scrolls).toBe(1);
  await page.emulateMedia({colorScheme: "dark"});
  await drawer.getByRole("button", {name: "Terminal actions"}).click();
  await drawer.getByRole("menu").getByText("Observe", {exact: true}).click();
  const continuation = screen.locator("span").filter({hasText: /^方式。$/});
  await expect(continuation).toHaveCSS("background-color", "rgb(68, 68, 68)");
  await page.emulateMedia({colorScheme: "light"});
  await expect(marker).toBeVisible();
  await expect(continuation).not.toHaveCSS("background-color", "rgb(68, 68, 68)");
  await expect(screen.locator(".xterm-bg-257").filter({hasText: "GPT-6.2-Sol"})).toBeVisible();
});

test("mobile control follows the visual viewport when a keyboard opens and closes", async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  await page.route(api, route => route.fulfill({json: projection}));
  const rows: number[] = [];
  await page.routeWebSocket("**/control", socket => {
    frame(socket, "Mobile input");
    socket.onMessage(raw => {
      const value = JSON.parse(raw.toString());
      if (value.type === "terminal.resize") rows.push(value.rows);
    });
  });
  const drawer = await dashboard(page);
  await expect(drawer.locator(".is-active .xterm-screen")).toContainText("Mobile input");
  await expect.poll(() => rows.length).toBeGreaterThan(0);
  const initial = rows.at(-1)!;
  async function keyboard(height: number) {
    await page.evaluate(value => {
      const viewport = window.visualViewport!;
      Object.defineProperty(viewport, "height", {configurable: true, value});
      viewport.dispatchEvent(new Event("resize"));
    }, height);
  }
  await keyboard(460);
  await expect.poll(async () => Math.round((await drawer.boundingBox())!.height)).toBe(460);
  await expect.poll(() => rows.at(-1)).toBeLessThan(initial);
  await keyboard(844);
  await expect.poll(() => rows.at(-1)).toBe(initial);
});
