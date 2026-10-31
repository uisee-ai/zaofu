import { test, expect } from "@playwright/test";

test("an undeclared Plan remains readable without a full PRD claim", async ({ page, request }) => {
  const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
  const plan = "evt-preview-undeclared";
  const response = await request.get(`/api/projects/${project}/plans/${plan}/preview`);
  expect(response.status()).toBe(200);
  expect((await response.json()).content_profile).toBe("derived_plan");
  await page.goto(`/?project=${project}&page=inbox&plan=${plan}`);
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  await dialog.getByRole("button", { name: "工程上下文", exact: true }).click();
  await expect(dialog.getByText("派生 Plan（需核对完整性）", { exact: true })).toBeVisible();
  await expect(dialog.getByText("完整 PRD Preview", { exact: true })).toHaveCount(0);
  await dialog.getByRole("button", { name: "完整原文", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "完整规划示例", exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Approve", exact: true })).toBeEnabled();
});
