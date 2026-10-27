import { test, expect as baseExpect } from "@playwright/test";

const expect = baseExpect.configure({ timeout: 15000 });

async function openReview(page: import("@playwright/test").Page, request: import("@playwright/test").APIRequestContext) {
  const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-review`);
  return page.getByRole("dialog", { name: "Plan preview" });
}

test("bound review layers expose decisions, module rules and concrete acceptance without writes", async ({ page, request }) => {
  const mutations: string[] = [];
  page.on("request", req => { if (req.method() !== "GET" && req.url().includes("/api/")) mutations.push(req.url()); });
  const dialog = await openReview(page, request);
  await expect(dialog.getByText("2 个任务 · 2 个验收项 · 2 个页面 · 1 类表单")).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "产品目标" })).toBeVisible();
  await dialog.locator("summary").filter({ hasText: "确认库存来源" }).click();
  await expect(dialog.getByText("关闭条件：确认唯一真源")).toBeVisible();
  await dialog.getByRole("button", { name: "模块规格", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "使用者与字段" })).toBeVisible();
  await expect(dialog.getByText("A01 库存；F01 修正", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "验收场景", exact: true }).click();
  await expect(dialog.getByText("库存8，重试后仍为8", { exact: true })).toBeVisible();
  await dialog.getByRole("combobox", { name: "审阅模块" }).selectOption("T-LEGACY");
  await expect(dialog.getByRole("status")).toContainText("尚未提供逐条 Given");
  await expect(dialog.getByText("受权读取", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(dialog.getByRole("combobox", { name: "审阅模块" })).toBeVisible();
  const pane = dialog.getByRole("region", { name: "计划审阅内容" });
  expect(await pane.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
  await dialog.getByRole("button", { name: "完整原文", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "完整规划示例" })).toBeVisible();
  expect(mutations).toEqual([]);
});

test("an unreadable bound port stays explicit and the original Plan remains readable", async ({ page, request }) => {
  await page.route("**/plans/evt-preview-review/artifacts/execution_spec", route => route.fulfill({ status: 409, body: "digest mismatch" }));
  const dialog = await openReview(page, request);
  await expect(dialog.getByRole("alert")).toContainText("execution_spec：HTTP 409");
  await dialog.getByRole("button", { name: "模块规格", exact: true }).click();
  await expect(dialog.getByRole("status")).toContainText("本版未细化独立模块规格");
  await dialog.getByRole("button", { name: "完整原文", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "完整规划示例" })).toBeVisible();
});

test("late artifact fetch cannot contaminate the next Plan", async ({ page, request }) => {
  let release!: () => void;
  const delay = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/plans/evt-preview-review/artifacts/execution_spec", async route => {
    const response = await route.fetch();
    await delay;
    await route.fulfill({ response }).catch(() => {});
  });
  const dialog = await openReview(page, request);
  await expect(dialog.getByText("正在读取同版规格…")).toBeVisible();
  await dialog.getByRole("button", { name: "Close preview" }).click();
  const next = page.getByTestId("inbox-queue-item").filter({ hasText: "evt-preview-digest" });
  await next.click();
  await page.locator(".inbox-reading-pane").getByRole("button", { name: "Preview", exact: true }).click();
  release();
  await expect(dialog.getByText("机械检查摘要。", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "模块规格", exact: true }).click();
  await expect(dialog.getByText("修正库存", { exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("status").filter({ hasText: "未取得同版任务明细" })).toBeVisible();
});
