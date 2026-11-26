import { expect, test } from "@playwright/test";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import path from "node:path";
const capture = JSON.parse(readFileSync(new URL("./fixtures/herdr-codex-prompt.json", import.meta.url), "utf8"));

const fixture = process.env.ZF_WEB_TERMINAL_FIXTURE;

test("Herdr terminal survives uncertainty and uses agent identity, clicks and observer recovery", async ({ page, request }) => {
  test.skip(!fixture || !!process.env.ZF_WEB_HERDR_HOST_SMOKE, "Requires isolated fake-herdr");
  test.setTimeout(60_000);
  const root = fixture!;
  const native = randomUUID();
  const setScenario = (extra: Record<string, unknown>) => {
    const file = path.join(root, "fake", "scenario.json");
    writeFileSync(file + ".next", JSON.stringify({ version: "0.9.5", native_id: native, ...extra }));
    renameSync(file + ".next", file);
  };
  mkdirSync(path.join(root, "fake"), { recursive: true });
  // Herdr 0.9.5 frame attributes captured from a submitted Codex prompt.
  const bg = "\x1b[48;2;68;68;68m";
  const ink = "\x1b[38;2;238;238;238m";
  const reset = "\x1b[0m";
  const pad = bg + " ".repeat(60) + reset;
  const sample = [
    ink + bg + "Unrelated output" + reset,
    "", pad, bg + ink + "\x1b[1;2m› " + reset + bg + ink + "介绍下你自己" + reset,
    bg + ink + "  Continued line" + reset, pad, "Reply", ...capture.ansi_rows, "",
  ].join("\r\n");
  setScenario({ status: "blocked", sample_output: sample });
  const dir = path.join(root, "codex", "sessions", "2026", "11", "23");
  mkdirSync(dir, { recursive: true });
  for (const [id, tokens] of [[native, 81], [randomUUID(), 914]] as const) {
    const rows = [
      { type: "session_meta", payload: { id, cwd: path.join(root, "project") } },
      { type: "event_msg", payload: { type: "token_count", info: {
        model: "gpt-5", total_token_usage: { input_tokens: tokens, output_tokens: 2 },
      } } },
    ];
    writeFileSync(path.join(dir, `rollout-test-${id}.jsonl`), rows.map(r => JSON.stringify(r)).join("\n") + "\n");
  }
  const headers = { "x-zf-web-token": process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST! };
  await page.addInitScript(token => localStorage.setItem("zf.webActionToken", token), headers["x-zf-web-token"]);
  await page.emulateMedia({ colorScheme: "light" });
  await page.addInitScript(() => localStorage.setItem("zf.themeMode", "system"));
  const mouseCommands: string[] = [];
  const submitted: string[] = [];
  page.on("websocket", socket => {
    if (new URL(socket.url()).pathname.endsWith("/control")) {
      socket.on("framesent", ({ payload }) => {
        const command = JSON.parse(String(payload));
        if (command.type === "terminal.input" && command.text === "\r") submitted.push(command.text);
        if (String(payload).includes('"terminal.mouse"')) mouseCommands.push(String(payload));
      });
    }
  });
  await page.goto("/");
  const skip = page.getByTestId("welcome-skip");
  if (await skip.waitFor({ state: "visible", timeout: 3000 }).then(() => true, () => false)) await skip.click();
  await page.getByRole("button", { name: "Toggle Terminal" }).click();
  const drawer = page.getByRole("complementary", { name: "Coding Agent Terminal" });
  await drawer.getByRole("button", { name: "New terminal" }).click();
  await drawer.getByRole("menuitem", { name: "New Codex terminal" }).click();
  const tab = drawer.getByRole("tab", { selected: true });
  await expect(tab).toContainText("Needs input");
  const id = await tab.getAttribute("data-session-id");
  const api = "/api/projects/default/terminal-sessions";
  const record = async () => (await (await request.get(api)).json()).sessions.find((row: { session_id: string }) => row.session_id === id);
  expect((await record()).usage.total_tokens).toBe(83);
  const screen = drawer.locator(".is-active .xterm-screen");
  await expect(screen).toContainText("Coding agent ready");
  const prompt = screen.getByText("介绍下你自己", { exact: true });
  const continued = screen.getByText("Continued line", { exact: true });
  const originalSurface = "rgb(68, 68, 68)";
  const colors = await drawer.evaluate(el => {
    const probe = document.createElement("span");
    probe.style.cssText = "color:var(--wt-text);background-color:var(--wt-surface)";
    el.appendChild(probe);
    const { color, backgroundColor } = getComputedStyle(probe);
    probe.remove();
    return { color, backgroundColor };
  });
  await expect(prompt).toHaveCSS("background-color", colors.backgroundColor);
  await expect(prompt).toHaveCSS("color", colors.color);
  await expect(continued).toHaveCSS("background-color", colors.backgroundColor);
  await expect(screen.getByText("Unrelated output")).toHaveCSS("background-color", originalSurface);
  const model = screen.locator("span.xterm-bg-257").filter({ hasText: "GPT-6.2-Sol" });
  const menuSurface = await model.evaluate(el => getComputedStyle(el).backgroundColor);
  expect(menuSurface).not.toEqual("rgba(0, 0, 0, 0)");
  const history = screen.locator(".web-terminal-codex-prompt").filter({ hasText: "请检查界面排版" });
  await expect(history.first()).toBeVisible();
  const screenshotDir = process.env.ZF_TERMINAL_SCREENSHOTS;
  if (screenshotDir) await drawer.screenshot({path: path.join(screenshotDir, "prompt-light.png")});
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(prompt).toHaveCSS("background-color", originalSurface);
  if (screenshotDir) await drawer.screenshot({path: path.join(screenshotDir, "prompt-dark.png")});
  await page.emulateMedia({ colorScheme: "light" });
  await expect(prompt).toHaveCSS("background-color", colors.backgroundColor);

  const pos = (await prompt.boundingBox())!;
  await page.mouse.move(pos.x + 1, pos.y + pos.height / 2);
  await page.mouse.down();
  await page.mouse.move(pos.x + pos.width - 1, pos.y + pos.height / 2, { steps: 8 });
  await page.mouse.up();
  await expect(screen.locator(".xterm-selection > div").first()).toBeVisible();
  await expect(screen.locator(".xterm-decoration-top").first()).toHaveCSS("background-color", "rgb(207, 226, 255)");
  const bounds = (await screen.boundingBox())!;
  await page.mouse.move(bounds.x + 3, bounds.y + 8);
  await page.mouse.down();
  await page.mouse.move(bounds.x + 70, bounds.y + 8, { steps: 5 });
  await page.mouse.up();
  await screen.click({ position: { x: 30, y: 30 }, modifiers: ["Shift"] });
  expect(mouseCommands).toHaveLength(0);
  await screen.click({ position: { x: 2, y: 2 } });
  await expect(screen).toContainText("mouse down 0,0");
  await expect(screen).toContainText("mouse up 0,0");
  expect(mouseCommands).toHaveLength(2);

  setScenario({ status: "working", tab_error: "protocol_mismatch" });
  expect((await request.post(api + "/reconcile", { headers })).status()).toBe(503);
  expect((await record()).state).toBe("active");
  await drawer.getByRole("button", { name: "Refresh terminal sessions" }).click();
  await expect(tab).toContainText("Working");
  let observers = 0;
  const observerCommands: string[] = [];
  const controlWidth = await screen.evaluate(el => el.clientWidth);
  const phone = await page.context().browser()!.newContext({
    viewport: { width: 390, height: 844 }, storageState: await page.context().storageState(),
  });
  const mirror = await phone.newPage();
  await mirror.goto(page.url());
  await mirror.getByRole("button", { name: "Toggle Terminal" }).click();
  const viewer = mirror.getByRole("complementary", { name: "Coding Agent Terminal" });
  const observersWithFullFrame = new Set<number>();
  mirror.on("websocket", socket => {
    if (new URL(socket.url()).pathname.endsWith("/observe")) {
      const connection = ++observers;
      socket.on("framesent", frame => observerCommands.push(String(frame.payload)));
      socket.on("framereceived", ({ payload }) => {
        if (typeof payload === "string" && JSON.parse(payload).full) observersWithFullFrame.add(connection);
      });
    }
  });
  writeFileSync(path.join(root, "fake", "interrupt-next-observer"), "");
  await viewer.getByRole("button", { name: "Terminal actions" }).click();
  await viewer.getByRole("menu").getByText("Observe", { exact: true }).click();
  await expect.poll(() => observers).toBeGreaterThanOrEqual(2);
  await expect.poll(() => observersWithFullFrame.size).toBeGreaterThanOrEqual(2);
  await expect(viewer.locator(".is-active .web-terminal-view")).toHaveAttribute("data-terminal-status", "observing");
  const mirrorScreen = viewer.locator(".is-active .xterm-screen");
  await expect(mirrorScreen).toContainText("Coding agent ready");
  await mirrorScreen.click({ position: { x: 30, y: 30 } });
  await expect(mirrorScreen).toContainText("Select Model and Effort");
  await expect(mirrorScreen.locator(".web-terminal-codex-prompt").filter({hasText: "第二行包含"})).toBeVisible();
  await mirror.setViewportSize({ width: 844, height: 390 });
  await expect(mirrorScreen).toContainText("Coding agent ready");
  expect(await screen.evaluate(el => el.clientWidth)).toBe(controlWidth);
  expect(observerCommands).toEqual([]);
  await screen.click({ position: { x: 2, y: 2 } });
  await page.keyboard.type("controller-alive");
  await expect(screen).toContainText("controller-alive");
  await page.keyboard.press("Enter");
  await expect.poll(() => submitted.length).toBe(1);
  await phone.close();
  setScenario({ tab_error: "tab_not_found" });
  expect((await request.post(api + "/reconcile", { headers })).ok()).toBeTruthy();
  expect((await record()).state).toBe("missing");
});

test("official Herdr and Codex create, observe, regain control and stop the same PTY", async ({ page, request }) => {
  test.skip(!process.env.ZF_WEB_HERDR_HOST_SMOKE, "Requires a dedicated real Herdr 0.9.5 host");
  test.setTimeout(90_000);
  const headers = { "x-zf-web-token": process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST! };
  const api = "/api/projects/default/terminal-sessions";
  const listing = await (await request.get(api)).json();
  expect(listing.capability).toMatchObject({ available: true, version: "0.9.5", mouse: true });
  await page.addInitScript(token => localStorage.setItem("zf.webActionToken", token), headers["x-zf-web-token"]);
  await page.goto("/");
  const skip = page.getByTestId("welcome-skip");
  if (await skip.waitFor({ state: "visible", timeout: 3000 }).then(() => true, () => false)) await skip.click();
  await page.getByRole("button", { name: "Toggle Terminal" }).click();
  const drawer = page.getByRole("complementary", { name: "Coding Agent Terminal" });
  const frames: string[] = [];
  let controlColumns = 0;
  page.on("websocket", socket => socket.on("framereceived", ({ payload }) => {
    if (typeof payload !== "string") return;
    if (!payload.includes('"terminal.frame"')) return;
    const meta = JSON.parse(payload);
    const endpoint = new URL(socket.url()).pathname;
    if (meta.full) frames.push(endpoint);
    if (endpoint.endsWith("/control")) controlColumns = meta.width;
  }));
  await drawer.getByRole("button", { name: "New terminal" }).click();
  const created = page.waitForResponse(r => r.request().method() === "POST" && r.url().endsWith("/terminal-sessions"));
  await drawer.getByRole("menuitem", { name: "New Codex terminal" }).click();
  const id: string = (await (await created).json()).session.session_id;
  const tab = drawer.locator(`[role="tab"][data-session-id="${id}"]`);
  await expect(tab).toHaveAttribute("aria-selected", "true");
  await expect(tab.locator("[data-agent-status]")).toBeVisible({ timeout: 30_000 });

  try {
    await expect.poll(() => frames.some(p => p.endsWith(`/${id}/control`))).toBeTruthy();
    const phone = await page.context().browser()!.newContext({
      viewport: {width: 390, height: 844}, storageState: await page.context().storageState(),
    });
    try {
      const mirror = await phone.newPage();
      const observeCommands: string[] = [];
      mirror.on("websocket", socket => {
        if (new URL(socket.url()).pathname.endsWith("/observe")) {
          socket.on("framesent", event => observeCommands.push(String(event.payload)));
        }
      });
      await mirror.goto(page.url());
      await mirror.getByRole("button", {name: "Toggle Terminal"}).click();
      const observer = mirror.getByRole("complementary", {name: "Coding Agent Terminal"});
      await observer.getByRole("button", {name: "Terminal actions"}).click();
      await observer.getByRole("menu").getByText("Observe", {exact: true}).click();
      const view = observer.locator(".is-active .web-terminal-view");
      await expect(view).toHaveAttribute("data-terminal-status", "observing");
      const columns = controlColumns;
      await mirror.setViewportSize({width: 844, height: 390});
      await expect(drawer.locator(".is-active .web-terminal-view")).toHaveAttribute("data-terminal-status", "controlling");
      expect(controlColumns).toBe(columns);
      expect(observeCommands).toEqual([]);
    } finally {
      await phone.close();
    }
    await drawer.getByRole("button", { name: "Terminal actions" }).click();
    await drawer.getByRole("menu").getByText("Observe", { exact: true }).click();
    await expect.poll(() => frames.some(p => p.endsWith(`/${id}/observe`))).toBeTruthy();
    await drawer.getByRole("button", { name: "Terminal actions" }).click();
    await drawer.getByRole("menu").getByText("Take over control", { exact: true }).click();
    await expect.poll(() => frames.filter(p => p.endsWith(`/${id}/control`)).length).toBeGreaterThan(1);
    expect((await request.post(api + "/reconcile", { headers })).ok()).toBeTruthy();
  } finally {
    const stopped = await request.post(`${api}/${id}/stop`, { headers });
    expect((await stopped.json()).session.state).toBe("stopped");
  }
});
