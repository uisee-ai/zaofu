import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";

type Event = { id: string; seq: number; type: string; payload?: Record<string, any> };

// A separate journey: initial approval, real execution, regenerated approval.
// The operator reviews the actual candidate and supplies its exact binding.
export async function approveExecutionReplan(
  page: Page, request: APIRequestContext, project: string, initialPlan: string,
  evidence: string, timeout: number, events: () => Promise<Event[]>,
) {
  let next: Event | undefined;
  await expect.poll(async () => {
    const rows = await events();
    const execution = rows.find(row => row.type === "task.pipeline.stage.dispatched");
    next = rows.find(row => row.type === "plan.approval.requested" &&
      row.payload?.plan_id !== initialPlan && execution && row.seq > execution.seq);
    return Boolean(next);
  }, { timeout, intervals: [2000] }).toBe(true);
  const planId = String(next!.payload!.plan_id);
  const response = await request.get(`/api/projects/${project}/plans/${encodeURIComponent(planId)}/preview`);
  expect(response.ok()).toBe(true);
  const body = await response.json();
  const binding = { plan_id: planId, sha256: createHash("sha256").update(body.markdown).digest("hex") };
  writeFileSync(`${evidence}/execution-replan-preview.json`, JSON.stringify(body, null, 2));
  writeFileSync(`${evidence}/execution-replan-ready.json`, JSON.stringify(binding));
  await page.goto(`/?project=${project}&page=inbox&plan=${encodeURIComponent(planId)}`);
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  await expect(dialog).toBeVisible();
  await page.screenshot({ path: `${evidence}/execution-replan-preview.png`, fullPage: true });
  await expect.poll(() => {
    const path = `${evidence}/execution-replan-approved.json`;
    if (!existsSync(path)) return false;
    const approved = JSON.parse(readFileSync(path, "utf8"));
    return approved.plan_id === binding.plan_id && approved.sha256 === binding.sha256;
  }, { timeout, intervals: [2000] }).toBe(true);
  await dialog.getByRole("button", { name: "Approve", exact: true }).click();
}
