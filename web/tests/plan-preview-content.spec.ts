import { test, expect } from "@playwright/test";

// Uses tests.plan_preview_app's actual FastAPI + React app, not page.setContent.
test("complete Plan, bound matrix and summary remain distinct without writes", async ({ page, request }) => {
  const snapshot = await (await request.get("/api/snapshot")).json();
  const project = snapshot.project.project_id;
  const mutations: string[] = [];
  page.on("request", (req) => {
    if (req.method() !== "GET" && req.url().includes("/api/")) mutations.push(req.url());
  });
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-complete`);
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  await expect(dialog.getByText("完整 Plan", { exact: true })).toBeVisible();
  const tail = dialog.getByText("END-OF-FULL-PLAN", { exact: true });
  await tail.scrollIntoViewIfNeeded();
  await expect(tail).toBeVisible();
  await expect(dialog.getByText("Q15 保持待确认。", { exact: true })).toBeVisible();
  await dialog.getByText("计划摘要（机械检查）", { exact: true }).click();
  await expect(dialog.getByText("机械检查摘要。", { exact: true })).toBeVisible();
  const matrix = dialog.getByRole("link", { name: "acceptance_matrix", exact: true });
  const response = await request.get((await matrix.getAttribute("href"))!);
  expect(response.status()).toBe(200);
  expect(await response.text()).toContain("END-OF-MATRIX");
  expect(mutations).toEqual([]);
});

test("missing full Plan is explicit; legacy digest is identified", async ({ page, request }) => {
  const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-missing`);
  let dialog = page.getByRole("dialog", { name: "Plan preview" });
  await expect(dialog.getByRole("alert")).toContainText("完整计划不可用");
  await expect(dialog.getByRole("button", { name: "Approve", exact: true })).toBeDisabled();
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-digest`);
  dialog = page.getByRole("dialog", { name: "Plan preview" });
  await expect(dialog.getByRole("status")).toContainText("仅有计划摘要");
  await expect(dialog.getByText("机械检查摘要。", { exact: true })).toBeVisible();
});

test("a late response from a closed preview cannot replace the newly selected Plan", async ({ page, request }) => {
  const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
  let release!: () => void;
  let completed!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  const fulfilled = new Promise<void>((resolve) => { completed = resolve; });
  await page.route("**/plans/evt-preview-complete/preview", async (route) => {
    const response = await route.fetch();
    await delayed;
    await route.fulfill({ response });
    completed();
  });
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-complete`);
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  await expect(dialog.getByText("Loading...", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "Close preview" }).click();
  const next = page.getByTestId("approval-card").filter({ hasText: "evt-preview-digest" });
  await next.click();
  await next.getByRole("button", { name: "Preview", exact: true }).click();
  await expect(dialog.getByRole("status")).toContainText("仅有计划摘要");
  const lateResponse = page.waitForResponse("**/plans/evt-preview-complete/preview");
  release();
  await fulfilled;
  await (await lateResponse).finished();
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(dialog.getByRole("status")).toContainText("仅有计划摘要");
  await expect(dialog.getByText("evt-preview-digest", { exact: true })).toBeVisible();
  await expect(dialog.getByText("完整规划示例", { exact: true })).toHaveCount(0);
});
