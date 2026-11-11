import { test, expect, type Page, type APIRequestContext } from "@playwright/test";
import live from "./fixtures/prd205/live-plan.json" with { type: "json" };

// Replay the actual v5 author's shapes through the real React preview entry.
// Only the projection responses are fixtures; no approval or provider is run.
async function openLive(page: Page, request: APIRequestContext, markdown = live.markdown, original = true) {
  const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
  await page.route("**/plans/evt-preview-review/preview", async route => {
    const response = await route.fetch();
    const body = await response.json();
    const names = ["task_map", "test_matrix", "source_index"];
    await route.fulfill({ response, json: { ...body, markdown, content_profile: "derived_plan_full", review_dossier_markdown: "",
      artifacts: body.artifacts.filter((p: { name: string }) => names.includes(p.name)),
      decision_review: { available: false, items: [], counts: { total: 0 }, error: "" },
    } });
  });
  for (const name of ["task_map", "test_matrix", "source_index"] as const) {
    await page.route(`**/plans/evt-preview-review/artifacts/${name}`, route => route.fulfill({ json: live[name] }));
  }
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-review`);
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  if (original) await dialog.getByRole("button", { name: "完整原文", exact: true }).click();
  return dialog;
}

test("real author shape: default approval overview, unknown register quotes decisions, 21 cases remain NOT_RUN", async ({ page, request }) => {
  const dialog = await openLive(page, request, live.markdown, false);
  await expect(dialog.getByRole("button", { name: "审批概览", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(dialog.getByText("完整 Plan Preview", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "完整原文", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "个人阅读清单：PRD、设计与执行候选 Plan", exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "待决事项", exact: true }).click();
  await expect(dialog.getByText(/结构化登记未确认，数量未知/)).toBeVisible();
  await expect(dialog.locator("td").filter({ hasText: "D-3 / pending_approval" })).toBeVisible();
  await expect(dialog.getByText(/共 0 条登记/)).toHaveCount(0);
  await dialog.getByRole("button", { name: "验收场景", exact: true }).click();
  await dialog.getByRole("combobox", { name: "审阅模块" }).selectOption("RL-READING");
  await expect(dialog.getByText("证据状态：NOT_RUN", { exact: true })).toHaveCount(21);
  await expect(dialog.getByRole("heading", { name: "ADD-TRIM", exact: true })).toBeVisible();
  await dialog.getByRole("combobox", { name: "审阅模块" }).selectOption("RL-SCAFFOLD");
  await expect(dialog.getByRole("heading", { name: "ADD-TRIM", exact: true })).toHaveCount(0);
  await dialog.getByRole("button", { name: "页面与设计", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "低保真页面与交互设计", exact: true })).toBeVisible();
});

test("rendered search crosses emphasis, navigates matches, and download preserves original bytes", async ({ page, request }) => {
  const markdown = "# 阅读测试\n\n## 第一节\n保存**失败**后保留输入。\n\n## 第二节\n保存失败可以重试。\n";
  const dialog = await openLive(page, request, markdown);
  await dialog.getByLabel("搜索正文", { exact: true }).fill("保存失败");
  await expect(dialog.getByText("1 / 2 处匹配", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "下一处", exact: true }).click();
  await expect(dialog.getByText("2 / 2 处匹配", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => Array.from(CSS.highlights.values()).some(h => h.size === 2))).toBe(true);
  const download = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "下载 Markdown", exact: true }).click();
  const stream = await (await download).createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  expect(Buffer.concat(chunks).toString()).toBe(markdown);
});

test("local diagrams, code highlighting, invalid-diagram fallback and sandbox isolation", async ({ page, request }) => {
  const external: string[] = [];
  page.on("request", r => { if (/^https?:/.test(r.url()) && new URL(r.url()).hostname !== "127.0.0.1") external.push(r.url()); });
  const dialog = await openLive(page, request, '# 图表\n\n```mermaid\nflowchart TD\n A[开始] --> B[完成]\n```\n\n```javascript\nconst answer = 42;\n```\n');
  const frame = dialog.frameLocator('iframe[title="Mermaid 流程图"]');
  await expect(frame.locator("svg")).toBeVisible({ timeout: 20000 });
  await expect(dialog.locator("iframe")).toHaveAttribute("sandbox", "");
  await expect.poll(() => dialog.locator('[data-streamdown="code-block-body"] span[style]').evaluateAll(nodes => new Set(nodes.map(n => (n as HTMLElement).style.color).filter(c => c && c !== "inherit")).size)).toBeGreaterThan(1);
  await dialog.getByRole("button", { name: "查看图源码", exact: true }).click();
  await expect(dialog.locator("pre").filter({ hasText: "flowchart TD" })).toBeVisible();
  expect(external).toEqual([]);
  await page.unrouteAll({ behavior: "wait" });
  await openLive(page, request, '# 无效图\n\n```mermaid\nnot a diagram\n```');
  await expect(dialog.getByText("流程图无法渲染，请查看源码检查图语法。", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "查看图源码", exact: true }).click();
  await expect(dialog.locator("pre").filter({ hasText: "not a diagram" })).toBeVisible();
  await page.unrouteAll({ behavior: "wait" });
  await openLive(page, request, '# 新图型\n\n```mermaid\narchitecture-beta\n service api(server)[API]\n```');
  await expect(dialog.getByText("该图类型暂不支持图形预览，请查看源码；可使用 flowchart、sequenceDiagram 或 C4 表达。", { exact: true })).toBeVisible();
});

test("375px table keeps readable columns and local scrolling instead of vertical character squeezing", async ({ page, request }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const dialog = await openLive(page, request);
  await dialog.locator("summary").filter({ hasText: "章节索引" }).click();
  await dialog.getByRole("button", { name: "Decision Register", exact: true }).click();
  await dialog.locator("summary").filter({ hasText: "章节索引" }).click();
  const cell = dialog.locator("td").filter({ hasText: "D-3 / pending_approval" });
  await cell.scrollIntoViewIfNeeded();
  expect((await cell.boundingBox())!.width).toBeGreaterThan(100);
  const wrap = cell.locator('xpath=ancestor::div[contains(@class,"agent-markdown-table-wrap")]');
  expect(await wrap.evaluate(el => el.scrollWidth > el.clientWidth)).toBe(true);
  expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
  await wrap.focus();
  await page.keyboard.press("ArrowRight");
  await expect.poll(() => wrap.evaluate(el => el.scrollLeft)).toBeGreaterThan(0);
});

test("read-only first visit bypasses setup and preview does not attempt read receipts", async ({ page, request }) => {
  await page.route("**/api/web-session", route => route.fulfill({ json: { mode: "read_only", actions_enabled: false } }));
  await page.route("**/api/workspace/onboarding", async route => {
    const response = await route.fetch(); await route.fulfill({ response, json: { ...await response.json(), show_welcome: true } });
  });
  await page.route("**/operator/inbox", async route => {
    const response = await route.fetch(); const body = await response.json();
    for (const key of ["items", "pending"]) body[key] = (body[key] || []).map((item: object) => ({ ...item, unread: true }));
    await route.fulfill({ response, json: body });
  });
  const writes: string[] = [];
  page.on("request", req => { if (req.method() !== "GET" && req.url().includes("/api/")) writes.push(req.url()); });
  await openLive(page, request);
  await expect(page.getByRole("dialog", { name: "Plan preview" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "个人阅读清单：PRD、设计与执行候选 Plan", exact: true })).toBeVisible();
  await page.unrouteAll({ behavior: "wait" });
  expect(writes).toEqual([]);
});

for (const width of [1280, 375]) test(`${width}px bound task specifications quote exact sections and expose shared contracts without inventing approval`, async ({ page, request }) => {
  await page.setViewportSize({ width, height: 900 });
  const markdown = live.markdown + "\n\n## Shared Spec\n\n共享快照必须先保存再发布。\n\n## Task Spec: RL-SCAFFOLD\n\n骨架任务专属说明。\n\n## Task Spec: RL-READING\n\n### 本片结果\n\n添加失败保留表单与原存储。\n\n### 验收\n\n引用 SAVE-ADD-FAIL，保持 NOT_RUN。\n";
  const dialog = await openLive(page, request, markdown);
  await dialog.getByRole("button", { name: "模块规格", exact: true }).click();
  await dialog.getByRole("combobox", { name: "审阅模块" }).selectOption("RL-READING");
  await expect(dialog.getByText("添加失败保留表单与原存储。", { exact: true })).toBeVisible();
  await expect(dialog.getByText("骨架任务专属说明。", { exact: true })).toHaveCount(0);
  await dialog.getByText("同版共享合同", { exact: true }).click();
  await expect(dialog.getByText("共享快照必须先保存再发布。", { exact: true })).toBeVisible();
  await dialog.getByText("任务责任与验证命令", { exact: true }).click();
  await expect(dialog.getByText("实现责任：dev-lane-1", { exact: true })).toBeVisible();
  await expect(dialog.getByLabel("验证命令 C-CANDIDATE", { exact: true })).toBeVisible();
  expect(await dialog.locator(".plan-review-scroll").evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
  await dialog.getByRole("button", { name: "在完整原文中阅读：Task Spec: RL-READING", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "完整原文", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(dialog.getByRole("heading", { name: "Task Spec: RL-READING", exact: true })).toBeInViewport();
});

test("duplicate or unknown task headings stay explicit and cannot replace another task specification", async ({ page, request }) => {
  const dialog = await openLive(page, request, "# Plan\n\n## Task Spec: RL-READING\n重复正文一\n## Task Spec: RL-READING\n重复正文二\n## Task Spec: UNKNOWN\n未知任务正文\n");
  await dialog.getByRole("button", { name: "模块规格", exact: true }).click();
  await dialog.getByRole("combobox", { name: "审阅模块" }).selectOption("RL-READING");
  await expect(dialog.getByText(/存在重复原文任务规格/)).toBeVisible();
  await expect(dialog.getByText("原文任务规格引用未知任务：UNKNOWN", { exact: true })).toBeVisible();
  await expect(dialog.getByText(/本版未细化独立模块规格/)).toBeVisible();
  await expect(dialog.getByText("重复正文一", { exact: true })).toHaveCount(0);
  await expect(dialog.getByText("未知任务正文", { exact: true })).toHaveCount(0);
});

for (const width of [1280, 375]) test(`${width}px sibling handoff stays reachable without changing task scope`, async ({ page, request }) => {
  await page.setViewportSize({ width, height: 900 });
  const markdown = "## Task Spec: RL-READING\n只负责本片。\n## TDD and evidence\n### 恢复\n失败后必须恢复原文件权限。\n## Task Spec: RL-SCAFFOLD\n其他任务专属。\n";
  const dialog = await openLive(page, request, markdown, false);
  await dialog.getByRole("button", { name: "模块规格", exact: true }).click();
  await dialog.getByRole("combobox", { name: "审阅模块" }).selectOption("RL-READING");
  await dialog.getByText("任务规格之外的原文章节（需核对适用性）", { exact: true }).click();
  await dialog.locator("summary").filter({ hasText: "TDD and evidence" }).click();
  await expect(dialog.getByText("失败后必须恢复原文件权限。", { exact: true })).toBeVisible();
  await expect(dialog.getByText("其他任务专属。", { exact: true })).toHaveCount(0);
  await dialog.getByRole("button", { name: "在完整原文中阅读：TDD and evidence", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "TDD and evidence", exact: true })).toBeInViewport();
});

for (const width of [1280, 375]) test(`${width}px real Chinese author headings populate the default overview`, async ({ page, request }) => {
  await page.setViewportSize({ width, height: 900 });
  for (const title of ["权威、范围与当前状态", "权威、范围与审批入口"]) {
    const markdown = `# 中文 Plan\n\n## ${title}\n仅交付三个文件，审批后实施。\n\n## PRD 用户结果\n原始字节不变。\n\n## Task Spec: RL-READING\n本片说明。`;
    const dialog = await openLive(page, request, markdown, false);
    await expect(dialog.getByRole("button", { name: "审批概览", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(dialog.getByText("仅交付三个文件，审批后实施。", { exact: true })).toBeVisible();
    await expect(dialog.getByText("原始字节不变。", { exact: true })).toBeVisible();
    await expect(dialog.getByText("本片说明。", { exact: true })).toHaveCount(0);
    await page.unrouteAll({ behavior: "wait" });
  }
});

for (const width of [1280, 375]) test(`${width}px unfamiliar opening is an explicit quotation, not an inferred approval summary`, async ({ page, request }) => {
  await page.setViewportSize({ width, height: 900 });
  const markdown = "# Plan\n## 权威、来源和授权\n候选仍等待 Owner 决定。\n### 来源\n保留来源原文。\n## PRD 用户结果\n原始字节不变。\n## Task Spec: RL-READING\n不能冒充概览的本片正文。\n";
  const dialog = await openLive(page, request, markdown, false);
  await expect(dialog.getByText("作者未声明独立概览；以下引用原文开篇，不推断审批结论。", { exact: true })).toBeVisible();
  await expect(dialog.getByText("候选仍等待 Owner 决定。", { exact: true })).toBeVisible();
  await expect(dialog.getByText("保留来源原文。", { exact: true })).toBeVisible();
  await expect(dialog.getByText("不能冒充概览的本片正文。", { exact: true })).toHaveCount(0);
  expect(await dialog.locator(".plan-review-scroll").evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
  await dialog.getByRole("button", { name: "在完整原文中阅读：权威、来源和授权", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "权威、来源和授权", exact: true })).toBeInViewport();
});
