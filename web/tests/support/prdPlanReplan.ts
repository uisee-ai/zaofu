import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";

export async function verifyPlanReplan(page: Page, request: APIRequestContext, project: string,
  first: { approval: any; planBody: any }, evidence: string, timeout: number,
  events: () => Promise<any[]>) {
  const initial = `${evidence}/initial`;
  mkdirSync(initial, { recursive: true });
  writeFileSync(`${initial}/lineage.json`, JSON.stringify(first, null, 2));
  writeFileSync(`${initial}/generated-plan.md`, first.planBody.markdown);
  const reason = "本次只做已批准需求的可读性修订，不新增产品范围、不变更 Task ID、AC、命令和依赖。在完整 Plan 新增小节“失败后的恢复边界”，将现有失败后原数据保护、清理及重试责任用独立前提/动作/结果说明，并引用原有 Task/AC。保持全部原始验收和当前评审策略；重规划后等待 Owner 审批，不实施、不执行产品测试。";
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  await dialog.getByPlaceholder("reject reason", { exact: true }).fill(reason);
  await page.screenshot({ path: `${evidence}/09-before-replan.png`, fullPage: true });
  await dialog.getByRole("button", { name: "Reject", exact: true }).click();
  let rejection: any;
  let approval: any;
  await expect.poll(async () => {
    const rows = await events();
    rejection = rows.find(row => row.type === "plan.rejected" && row.payload?.plan_id === first.planBody.plan_id);
    if (rejection) approval = rows.find(row => row.type === "plan.approval.requested" && row.seq > rejection.seq);
    return Boolean(rejection && approval);
  }, { timeout, intervals: [2000] }).toBe(true);
  // Event-list projections omit non-index fields; hydrate this exact occurrence.
  const detail = await request.get(`/api/projects/${project}/events/${encodeURIComponent(rejection.id)}`);
  expect(detail.ok()).toBeTruthy();
  rejection = (await detail.json()).event;
  expect(rejection.payload.surface).toBe("web");
  expect(rejection.payload.reason).toBe(reason);
  const planId = String(approval.payload.plan_id);
  expect(planId).not.toBe(first.planBody.plan_id);
  const response = await request.get(`/api/projects/${project}/plans/${encodeURIComponent(planId)}/preview`);
  expect(response.ok()).toBeTruthy();
  const planBody = await response.json();
  expect(planBody.content_profile).toBe("derived_plan_full");
  expect(planBody.markdown).toContain("失败后的恢复边界");
  expect(planBody.markdown).not.toBe(first.planBody.markdown);
  await page.goto(`/?project=${project}&page=inbox&plan=${encodeURIComponent(planId)}`);
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "完整原文", exact: true }).click();
  await dialog.getByRole("button", { name: "查看源码", exact: true }).click();
  await expect(dialog.locator(".plan-document-source")).toHaveText(planBody.markdown);
  const ready = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "下载 Markdown", exact: true }).click();
  await (await ready).saveAs(`${evidence}/replan-download.md`);
  await page.reload();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "完整原文", exact: true }).click();
  await dialog.getByRole("button", { name: "查看源码", exact: true }).click();
  await expect(dialog.locator(".plan-document-source")).toHaveText(planBody.markdown);
  await dialog.getByRole("button", { name: "格式化阅读", exact: true }).click();
  await page.screenshot({ path: `${evidence}/10-replanned-preview.png`, fullPage: true });
  writeFileSync(`${evidence}/generated-plan.md`, planBody.markdown);
  writeFileSync(`${evidence}/replan.json`, JSON.stringify({ reason, rejection, approval,
    initial_plan_id: first.planBody.plan_id, final_plan_id: planId,
    initial_sha256: createHash("sha256").update(first.planBody.markdown).digest("hex"),
    final_sha256: createHash("sha256").update(planBody.markdown).digest("hex"),
    boundary: "owner-requested readability revision; no new product behavior or implementation" }, null, 2));
  return { approval, planBody, planId };
}
