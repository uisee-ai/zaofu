import { expect, test } from "@playwright/test";

test("a real Herdr PTY survives an overloaded observer and acknowledges the controller interrupt", async ({page, browser, request}) => {
  test.skip(!process.env.ZF_WEB_HERDR_PRESSURE, "Requires the isolated real Herdr pressure tier");
  test.setTimeout(180_000);
  const api = "/api/projects/default/terminal-sessions";
  const headers = {"x-zf-web-token": process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST!};
  const created = await request.post(api, {headers, data: {provider: "codex", slot: "load", title: "Pressure fixture"}});
  expect(created.ok()).toBe(true);
  const session = (await created.json()).session;
  const token = headers["x-zf-web-token"];
  await page.addInitScript(t => localStorage.setItem("zf.webActionToken", t), token);
  const phone = await browser.newContext({viewport: {width: 390, height: 844}});
  try {
    await page.goto("/");
    const skip = page.getByTestId("welcome-skip");
    if (await skip.waitFor({state: "visible", timeout: 2000}).then(() => true, () => false)) await skip.click();
    await page.getByRole("button", {name: "Toggle Terminal"}).click();
    const control = page.locator(".is-active .web-terminal-view");
    const screen = control.locator(".xterm-screen");
    await expect(screen).toContainText("PRESSURE READY pid=", {timeout: 20_000});
    const pid = (await screen.innerText()).match(/PRESSURE READY pid=(\d+)/)![1];
    const input = control.locator(".xterm-helper-textarea");
    const mirror = await phone.newPage();
    await mirror.addInitScript(t => localStorage.setItem("zf.webActionToken", t), token);
    await mirror.clock.install();
    const reports: Array<{bytes: number; receivedSeq: number; parsedSeq: number}> = [];
    const fullFrames: number[] = [];
    let connections = 0;
    mirror.on("console", async msg => {
      if (msg.text().startsWith("Terminal parser backlog")) reports.push(await msg.args()[1].jsonValue());
    });
    mirror.on("websocket", socket => {
      if (!new URL(socket.url()).pathname.endsWith("/" + session.session_id + "/observe")) return;
      const connection = ++connections;
      socket.on("framereceived", ({payload}) => {
        if (typeof payload === "string" && JSON.parse(payload).full) fullFrames.push(connection);
      });
    });
    await mirror.goto(page.url());
    await mirror.getByRole("button", {name: "Toggle Terminal"}).click();
    await mirror.getByRole("button", {name: "Terminal actions"}).click();
    await mirror.getByRole("menu").getByText("Observe", {exact: true}).click();
    const observer = mirror.locator(".is-active .web-terminal-view");
    await expect(observer.locator(".xterm-screen")).toContainText("PRESSURE READY pid=" + pid);
    await expect(observer.getByText("Read-only", {exact: true})).toBeVisible();
    const takeOver = observer.getByRole("button", {name: "Take over control"});
    await expect(takeOver).toBeInViewport();
    if (process.env.ZF_TERMINAL_SCREENSHOTS) {
      await mirror.screenshot({path: process.env.ZF_TERMINAL_SCREENSHOTS + "/mobile-observer.png"});
    }
    await mirror.clock.pauseAt(new Date(Date.now() + 2000));
    await input.pressSequentially("stream");
    await input.press("Enter");
    await expect(screen).toContainText(/PRESSURE \d+/);
    await expect(observer).toHaveAttribute("data-terminal-status", "overloaded", {timeout: 95_000});
    await expect(control).toHaveAttribute("data-terminal-status", "controlling");
    await page.bringToFront();
    await input.press("Control+c");
    await expect(screen).toContainText("INTERRUPTED pid=" + pid, {timeout: 15000});
    expect(reports[0].bytes).toBeGreaterThan(8 * 1024 * 1024);
    expect(reports[0].receivedSeq).toBeGreaterThan(reports[0].parsedSeq);
    await mirror.clock.resume();
    await observer.getByRole("button", {name: "Reconnect"}).click();
    await expect(observer.locator(".xterm-screen")).toContainText("INTERRUPTED pid=" + pid);
    expect(new Set(fullFrames).size).toBe(2);
    await input.pressSequentially("ping");
    await input.press("Enter");
    await expect(observer.locator(".xterm-screen")).toContainText("PONG pid=" + pid);
    const listing = await (await request.get(api)).json();
    expect(listing.sessions.find((s: {session_id: string}) => s.session_id === session.session_id))
      .toMatchObject({state: "active", generation: session.generation});
    await takeOver.click();
    await expect(observer).toHaveAttribute("data-terminal-status", "controlling");
    const confirmation = observer.getByRole("status").filter({hasText: "Control transferred. You can type now."});
    await expect(confirmation).toBeVisible();
    await expect(takeOver).toHaveCount(0);
    if (process.env.ZF_TERMINAL_SCREENSHOTS) {
      await mirror.screenshot({path: process.env.ZF_TERMINAL_SCREENSHOTS + "/mobile-controller.png"});
    }
    const phoneInput = observer.locator(".xterm-helper-textarea");
    await mirror.setViewportSize({width: 390, height: 460});
    await phoneInput.pressSequentially("mobile");
    await phoneInput.press("Enter");
    await expect(observer.locator(".xterm-screen")).toContainText("MOBILE pid=" + pid);
    await expect(confirmation).toHaveCount(0, {timeout: 7000});
    console.log("Real PTY pressure", {...reports[0], observerAttachments: connections, sameProcess: true});
  } finally {
    await phone.close();
    expect((await request.post(api + "/" + session.session_id + "/stop", {headers})).ok()).toBe(true);
  }
});
