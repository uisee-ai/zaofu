import { expect, test } from "@playwright/test";
import { writeFileSync } from "node:fs";

const evidence = "/zf-run/evidence";
test.setTimeout(900_000);
test.skip(process.env.ZF_UI_JOURNEY !== "channel-recovery" || process.env.ZF_UI_PROVIDER !== "real", "requires explicit real Codex recovery runner");

test("real Codex abort recovers through UI; lost completion never redelivers", async ({ page, request }) => {
  const snapshot = await (await request.get("/api/snapshot")).json();
  const project = snapshot.project.project_id;
  const ledger: any[] = [];
  async function events() {
    for (;;) {
      const cursor = ledger.at(-1)?.seq || 0;
      const response = await request.get(`/api/projects/${project}/events?cursor=${cursor}&limit=500`);
      expect(response.ok()).toBeTruthy();
      const rows = (await response.json()).items || [];
      ledger.push(...rows.filter((e: any) => e.seq > cursor));
      if (rows.length < 500) return ledger;
    }
  }
  async function waitFor(predicate: (e: any) => boolean) {
    let found: any;
    await expect.poll(async () => { found = (await events()).find(predicate); return Boolean(found); },
      { timeout: 360_000, intervals: [300, 1000, 2000] }).toBeTruthy();
    return found;
  }
  await page.addInitScript(token => localStorage.setItem("zf.webActionToken", token), process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST || "");
  for (const kind of ["abort", "completed"]) {
    writeFileSync("/zf-run/fault.json", JSON.stringify({ mode: kind }));
    const channel = `ch-codex-${kind}`;
    await page.goto(`/?project=${project}&page=channels&channel=${channel}`);
    const input = page.getByRole("textbox", { name: `Message Codex ${kind} recovery` });
    await expect(input).toBeEnabled();
    await input.fill("@recovery-reader 这是只读需求讨论，不授权 Git、文件写入或启动实施。请简短说明当前阶段与等待 Owner 审批的边界，最后回复 CODEX_RECOVERY_OK。不要运行工具。不要向其他 agent 转发。");
    await page.getByRole("button", { name: "Send message", exact: true }).click();
    const failed = await waitFor(e => e.type === "channel.agent.reply.failed" && e.payload.channel_id === channel);
    const channelDetail = await (await request.get(`/api/projects/${project}/channels/${channel}`)).json();
    expect(channelDetail.reply_requests.find((r: any) => r.request_id === failed.payload.request_id)?.failure_status).toBe("delivery_unknown");
    writeFileSync(`${evidence}/${kind}-failure.json`, JSON.stringify(failed, null, 2));
    const retry = page.getByRole("button", { name: "核验终止并重试原回复", exact: true }).last();
    // Recovery lives in the failures drawer; opening it is part of the user's path.
    const attention = page.getByTestId("channel-header-failures");
    if (!await retry.isVisible() && await attention.isVisible()) await attention.click();
    await expect(retry).toBeVisible({ timeout: 20_000 });
    await page.screenshot({ path: `${evidence}/${kind}-before-retry.png`, fullPage: true });
    const responsePromise = page.waitForResponse(r => r.url().includes("/actions/channel-drain-replies") && r.request().method() === "POST");
    await retry.click();
    const response = await responsePromise;
    const result = await response.json();
    writeFileSync(`${evidence}/${kind}-action.json`, JSON.stringify({ status: response.status(), result }, null, 2));
    if (kind === "abort") {
      expect(response.status()).toBe(202);
      expect(result.run_generation).toBe(2);
      await waitFor(e => e.type === "channel.agent.reply.completed" && e.payload.channel_id === channel);
      const drawerClose = page.getByTitle("Close drawer", { exact: true });
      if (await drawerClose.isVisible()) await drawerClose.click();
      await expect(page.locator(".channel-page")).toContainText("CODEX_RECOVERY_OK", { timeout: 20_000 });
    } else {
      expect(response.status()).toBe(409);
      expect(result.reason).toContain("禁止重复投递");
      await expect(page.locator(".channel-page")).toContainText("禁止重复投递");
    }
    await page.screenshot({ path: `${evidence}/${kind}-after-retry.png`, fullPage: true });
  }
  writeFileSync(`${evidence}/events.json`, JSON.stringify(await events(), null, 2));
});
