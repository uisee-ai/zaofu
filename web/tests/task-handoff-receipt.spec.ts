import { expect, test } from "@playwright/test";
import { installUiFixture, uiTasks } from "./ui-alignment.fixture";

for (const [status, label] of [
  ["awaiting_receipt", "材料已保存，等待接收确认"],
  ["accepted", "接手者已确认接收（不代表业务完成）"],
  ["incomplete", "交接证据不完整，需要修复"],
] as const) {
  test(`task displays ${status} independently of business completion`, async ({ page }) => {
    const { errors, writes } = await installUiFixture(page);
    await page.route("**/api/**/tasks/UI-102", (route) => route.fulfill({ json: {
      task: uiTasks[1], events: [], contract: {}, briefing: { text: "" }, artifacts: [],
      handoff_summary: { schema_version: "handoff-summary.v1", task_id: "UI-102", objective: "Finish remaining checks",
        current_state: "in_progress", current_stage: "impl",
        receipt: { status, delivery_status: "dispatched", target_operation_id: "op-successor", business_completed: false } },
    } }));
    await page.goto("/?project=ui-review&page=task&task=UI-102");
    await expect(page.locator(".reader-status")).toContainText("进行中");
    await expect(page.getByTestId("task-handoff-receipt")).toHaveCount(0);
    await page.getByRole("link", { name: "查看任务事件" }).click();
    await page.getByText(/查看任务运行详情 ·/).click();
    await expect(page.getByTestId("task-handoff-receipt")).toHaveText(label);
    await expect(page.locator(".handoff-summary-panel")).toContainText("in_progress");
    await expect(page.locator(".handoff-summary-panel")).toContainText("已派发到目标执行");
    await page.reload();
    await page.getByText(/查看任务运行详情 ·/).click();
    await expect(page.getByTestId("task-handoff-receipt")).toHaveText(label);
    await page.screenshot({ path: test.info().outputPath("receipt.png"), fullPage: true });
    expect(errors).toEqual([]);
    expect(writes).toEqual([]);
  });
}
