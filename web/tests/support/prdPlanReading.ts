import { expect, type APIRequestContext, type Locator, type Page } from "@playwright/test";
import { writeFileSync } from "node:fs";

/** Check the actual admitted API/UI, not a replacement response or authored summary. */
export async function verifyPlanModuleReading(page: Page, request: APIRequestContext, dialog: Locator, project: string, planId: string, evidence: string) {
  const response = await request.get(`/api/projects/${project}/plans/${encodeURIComponent(planId)}/artifacts/task_map`);
  expect(response.ok()).toBeTruthy();
  const taskMap = await response.json();
  const ids: string[] = taskMap.tasks.map((task: { task_id?: string; id?: string }) => task.task_id || task.id);
  expect(ids.length).toBeGreaterThan(0);
  expect(new Set(ids).size).toBe(ids.length);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await dialog.getByRole("button", { name: "模块规格", exact: true }).click();
  const observations = [];
  for (const id of ids) {
    await dialog.getByRole("combobox", { name: "审阅模块" }).selectOption(id);
    await expect(dialog.getByText("正在读取同版规格…")).toHaveCount(0);
    const pane = dialog.getByRole("region", { name: "计划审阅内容" });
    const text = await pane.innerText();
    const fallback = text.includes("本版未细化独立模块规格");
    const sourceShown = text.includes("规格来源：同版 execution_spec 任务分区")
      || text.includes("同版原文；与其他任务完全相同的章节");
    observations.push({ task_id: id, fallback, sourceShown, text });
    writeFileSync(`${evidence}/module-reading.json`, JSON.stringify(observations, null, 2));
    await page.screenshot({ path: `${evidence}/module-${observations.length}.png`, fullPage: true });
    expect(fallback, `${id}: final admitted Plan has no readable Task Spec`).toBe(false);
    expect(sourceShown, `${id}: module must quote a bound author specification`).toBe(true);
  }
}
