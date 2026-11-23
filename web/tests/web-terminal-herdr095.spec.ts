import { expect, test } from "@playwright/test";
import { mkdirSync, renameSync, writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import path from "node:path";

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
  setScenario({ status: "blocked" });
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
  const mouseCommands: string[] = [];
  page.on("websocket", socket => {
    if (new URL(socket.url()).pathname.endsWith("/control")) {
      socket.on("framesent", ({ payload }) => {
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
  const mirror = await page.context().newPage();
  await mirror.goto("/");
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
  expect(observerCommands).toEqual([]);
  await screen.click({ position: { x: 2, y: 2 } });
  await page.keyboard.type("controller-alive");
  await expect(screen).toContainText("controller-alive");
  await mirror.close();
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
  page.on("websocket", socket => socket.on("framereceived", ({ payload }) => {
    if (typeof payload !== "string") return;
    if (payload.includes('"terminal.frame"') && JSON.parse(payload).full) frames.push(new URL(socket.url()).pathname);
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
