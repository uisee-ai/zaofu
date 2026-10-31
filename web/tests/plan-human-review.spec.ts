import { test, expect as baseExpect } from "@playwright/test";

const expect = baseExpect.configure({ timeout: 15000 });

async function openReview(page: import("@playwright/test").Page, request: import("@playwright/test").APIRequestContext) {
  const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-review`);
  return page.getByRole("dialog", { name: "Plan preview" });
}

test("bound review layers expose decisions, module rules and concrete acceptance without writes", async ({ page, request }) => {
  const mutations: string[] = [];
  // Inbox read receipts are separate; the product reader must not mutate business state.
  page.on("request", req => { if (req.method() !== "GET" && req.url().includes("/api/") && !req.url().endsWith("/actions/inbox-item-read")) mutations.push(req.url()); });
  const dialog = await openReview(page, request);
  await dialog.getByRole("button", { name: "工程上下文", exact: true }).click();
  await expect(dialog.getByText("未覆盖: REQ-MISSING", { exact: true })).toBeVisible();
  await expect(dialog.getByText("来源条款 3，覆盖账本包含 2；映射存在不代表验收通过。", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "工程上下文", exact: true }).click();
  await expect(dialog.getByText("2 个任务 · 2 个验收项 · 2 个页面 · 1 类表单")).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "产品目标" })).toBeVisible();
  await expect(dialog.getByText("待决定 1 · 明确阻塞 0 · 已授权待落实 1 · 已关闭 0 · 其他状态 0", { exact: true })).toBeVisible();
  await dialog.locator("summary").filter({ hasText: "确认库存来源" }).click();
  await expect(dialog.getByText("关闭条件：确认唯一真源")).toBeVisible();
  await dialog.getByRole("button", { name: "模块规格", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "使用者与字段" })).toBeVisible();
  await expect(dialog.getByText("A01 库存；F01 修正", { exact: true })).toBeVisible();
  await dialog.getByRole("combobox", { name: "审阅模块" }).selectOption("T-LEGACY");
  await expect(dialog.getByRole("heading", { name: "读取规格" })).toBeVisible();
  await expect(dialog.getByText("只能查看授权范围，拒绝越权查询。")).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "使用者与字段" })).toHaveCount(0);
  await expect(dialog.getByText("A02 查询", { exact: true })).toBeVisible();
  await page.reload();
  await expect(dialog.getByRole("heading", { name: "读取规格" })).toBeVisible();
  await dialog.getByRole("combobox", { name: "审阅模块" }).selectOption("T-STOCK");
  await expect(dialog.getByRole("heading", { name: "使用者与字段" })).toBeVisible();
  await dialog.getByRole("button", { name: "验收场景", exact: true }).click();
  await expect(dialog.getByText("库存8，重试后仍为8", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "SC-STOCK 差额修正和重试" })).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "E01 · 正式重试检查" })).toBeVisible();
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

test("mobile product navigation keeps design status and searchable source chapters readable", async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const dialog = await openReview(page, request);
  await dialog.getByRole("button", { name: "页面与设计", exact: true }).click();
  await dialog.getByLabel("查找页面或表单", { exact: true }).fill("A01");
  await expect(dialog.getByRole("heading", { name: "A01 · 库存" })).toBeVisible();
  await expect(dialog.getByText("设计：待设计 · 实现：待实现 · 视觉验收：待视觉验收", { exact: true })).toBeVisible();
  await expect(dialog.getByText("参考素材：01、02", { exact: false })).toBeVisible();
  await dialog.getByRole("button", { name: "来源原文", exact: true }).click();
  await dialog.locator("summary").filter({ hasText: "章节索引" }).click();
  await dialog.getByLabel("搜索正文", { exact: true }).fill("重复扣减");
  await expect(dialog.getByText("1 个匹配章节", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "库存规则", exact: true }).click();
  await expect(page).toHaveURL(/anchor=stock/);
  await expect(dialog.getByRole("heading", { name: "库存规则" })).toBeVisible();
  expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
});

test("coverage links to bound sources, filters gaps and compares a historical version", async ({ page, request }) => {
  const dialog = await openReview(page, request);
  await dialog.getByRole("button", { name: "覆盖审查", exact: true }).click();
  await expect(dialog.getByText("3 条来源记录 · 1 条未分配 · 1 条待决定 · 1 条需核对引用", { exact: true })).toBeVisible();
  await dialog.getByLabel("覆盖状态", { exact: true }).selectOption("unmapped");
  await expect(dialog.locator(".plan-trace-list > details")).toHaveCount(1);
  await expect(dialog.locator(".plan-trace-list")).toContainText("REQ-MISSING");
  await dialog.getByLabel("覆盖状态", { exact: true }).selectOption("pending");
  await expect(dialog.locator(".plan-trace-list")).toContainText("REQ-NOTIFY");
  await dialog.getByLabel("覆盖状态", { exact: true }).selectOption("all");
  const stock = dialog.locator(".plan-trace-list > details").filter({ hasText: "REQ-STOCK" });
  await stock.locator("summary").first().click();
  await expect(stock.getByText("test stock", { exact: true })).toBeVisible();
  await stock.locator("summary").filter({ hasText: "来源条款" }).click();
  await stock.getByRole("link", { name: "artifacts/source.md#stock" }).click();
  await expect(dialog.getByRole("heading", { name: "库存规则" })).toBeVisible();
  await expect(page).toHaveURL(/anchor=stock/);
  await page.reload();
  await expect(dialog.getByRole("heading", { name: "库存规则" })).toBeVisible();
  await dialog.getByRole("button", { name: "覆盖审查", exact: true }).click();
  await dialog.locator("summary").filter({ hasText: "比较 Plan 版本" }).click();
  await dialog.getByLabel("对比 Plan ID", { exact: true }).fill("evt-preview-review-old");
  await dialog.getByRole("button", { name: "比较版本", exact: true }).click();
  await expect(dialog.getByText("evt-preview-review-old → evt-preview-review：3 条变化", { exact: true })).toBeVisible();
  await dialog.locator("summary").filter({ hasText: "修改 · REQ-STOCK" }).click();
  await expect(dialog.getByText("原文：旧版库存说明", { exact: true })).toBeVisible();
  await dialog.getByLabel("对比 Plan ID", { exact: true }).fill("evt-preview-digest");
  await dialog.getByRole("button", { name: "比较版本", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("无法确认两个 Plan 属于同一需求或流程");
  await expect(dialog.getByText("原文：旧版库存说明", { exact: true })).toHaveCount(0);
});

test("large coverage is bounded and a search can reach its final source", async ({ page, request }) => {
  await page.route("**/plans/evt-preview-review/artifacts/source_index", route => route.fulfill({ json: {
    rows: Array.from({ length: 2500 }, (_, i) => ({ source_id: `S${i}`, statement: `来源条款 ${i}` })),
  } }));
  const dialog = await openReview(page, request);
  await dialog.getByRole("button", { name: "覆盖审查", exact: true }).click();
  await expect(dialog.locator(".plan-trace-list > details")).toHaveCount(25);
  await dialog.getByLabel("搜索条款、任务或验收项", { exact: true }).fill("S2499");
  await expect(dialog.locator(".plan-trace-list > details")).toHaveCount(1);
  await expect(dialog.locator(".plan-trace-list")).toContainText("来源条款 2499");
  await dialog.locator("summary").filter({ hasText: "比较 Plan 版本" }).click();
  await page.route("**/plans/evt-preview-review-old/artifacts/source_index", route => route.fulfill({ json: {} }));
  await dialog.getByLabel("对比 Plan ID", { exact: true }).fill("evt-preview-review-old");
  await dialog.getByRole("button", { name: "比较版本", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("source_index：来源/覆盖列表缺失");
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
