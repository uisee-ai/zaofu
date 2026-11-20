import { test, expect } from "@playwright/test";

for (const [mode, label] of [
  ["none", "未安排独立 Critic 评审"],
  ["targeted", "定向评审 · 不代表全文通过"],
  ["full", "完整评审 · 配置不代表已通过"],
]) {
  test(`bound ${mode} review policy is visible without claiming approval`, async ({ page, request }, testInfo) => {
    const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
    const api = await request.get(`/api/projects/${project}/plans/evt-policy-${mode}/preview`);
    expect(api.ok()).toBeTruthy();
    expect((await api.json()).critic_review.mode).toBe(mode);
    const writes: string[] = [];
    page.on("request", req => {
      if (req.method() !== "GET" && req.url().includes("/api/") && !req.url().endsWith("/actions/inbox-item-read")) writes.push(req.url());
    });
    await page.goto(`/?project=${project}&page=inbox&plan=evt-policy-${mode}`);
    const panel = page.getByRole("dialog", { name: "Plan preview", exact: true });
    await panel.getByRole("button", { name: "审批概览", exact: true }).click();
    await expect(panel.getByText(label, { exact: true })).toBeVisible();
    if (mode === "targeted") await expect(panel.getByText("两个入口的失败状态与验收时点", { exact: true })).toBeVisible();
    expect(writes).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`${mode}-review.png`), fullPage: true });
  });
}


test("module AC resolves its authored source ID before opening global coverage", async ({ page, request }) => {
  const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-review`);
  const panel = page.getByRole("dialog", { name: "Plan preview", exact: true });
  await panel.getByRole("button", { name: "模块规格", exact: true }).click();
  await expect(panel.getByRole("heading", { name: "AC-STOCK", exact: true })).toBeVisible();
  const criterion = panel.locator("article").filter({ has: page.getByRole("heading", { name: "AC-STOCK", exact: true }) });
  await criterion.locator("summary").filter({ hasText: "来源条款" }).click();
  await expect(criterion.getByText("artifacts/source.md#stock", { exact: true })).toBeVisible();
  await expect(panel.getByText(/AC-STOCK 来源 ID 未唯一解析/)).toHaveCount(0);
});
