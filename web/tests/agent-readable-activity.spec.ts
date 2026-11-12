import { expect, test } from "@playwright/test";

test("shared Agent process is folded, readable, recoverable and fits mobile", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/?fixture=agent-session");
  const fixture = page.getByTestId("fx-readable-activity");
  await fixture.scrollIntoViewIfNeeded();
  const process = fixture.getByTestId("agent-readable-activity");
  await expect(process).toContainText("整理已确认决定");
  await expect(process).toContainText("已保存 2 / 5 部分");
  await expect(process.getByTestId("agent-readable-activity-body")).toHaveCount(0);
  await page.screenshot({ path: "test-results/readable-activity-collapsed.png" });
  await process.getByRole("button", { name: "展开过程", exact: true }).click();
  await expect(process.getByTestId("agent-readable-activity-body")).toBeVisible();
  await expect(process).toContainText("已恢复先前保存的部分");
  await expect(process).toContainText("待最终完整性校验");
  await expect(process).toContainText("正在核对已确认的身份配置");
  await expect(process).not.toContainText("channel_synthesis_part");
  await expect(fixture).not.toContainText("native-session-id-for-diagnostics");
  await process.getByText("查看 1 项活动", { exact: true }).click();
  await expect(process).toContainText("读取文件 · PRD-v2.md");
  await expect(process.locator(".agent-tool-row")).not.toBeVisible();
  await page.screenshot({ path: "test-results/readable-activity-expanded.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture.scrollIntoViewIfNeeded();
  const bounds = await process.evaluate(el => ({ width: el.scrollWidth, client: el.clientWidth,
    left: el.getBoundingClientRect().left, right: el.getBoundingClientRect().right, viewport: innerWidth }));
  expect(bounds.width).toBeLessThanOrEqual(bounds.client + 1);
  expect(bounds.left).toBeGreaterThanOrEqual(-1);
  expect(bounds.right).toBeLessThanOrEqual(bounds.viewport + 1);
  await page.screenshot({ path: "test-results/readable-activity-mobile.png" });
  expect(errors).toEqual([]);
});

test("Kanban and ordinary Channel Agents use the same folded activity", async ({ page }) => {
  await page.goto("/?fixture=agent-session");
  for (const id of ["fx-kanban-compact", "fx-channel-compact"]) {
    const fixture = page.getByTestId(id);
    const process = fixture.getByTestId("agent-readable-activity").last();
    await expect(process).toContainText("项活动");
    await expect(process.getByTestId("agent-readable-activity-body")).toHaveCount(0);
    await process.getByRole("button", { name: "展开过程", exact: true }).click();
    await expect(process.getByTestId("agent-readable-activity-body")).toBeVisible();
  }
});

test("a failed reply without tools keeps its terminal status and original reason", async ({ page }) => {
  await page.goto("/?fixture=agent-session");
  const activity = page.getByTestId("fx-readable-failure").getByTestId("agent-readable-activity");
  await expect(activity).toContainText("本次执行未完成");
  await activity.getByRole("button", { name: "展开过程", exact: true }).click();
  await expect(page.getByTestId("fx-readable-failure").locator(".agent-text-part.error")).toHaveCount(1);
  await expect(page.getByTestId("fx-readable-failure")).toContainText("连接中断；本次未完成，已保存的内容保留。");
  await expect(activity.locator(".agent-activity-spinner")).toHaveCount(0);
});
