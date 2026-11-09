import { test, expect } from "@playwright/test";

for (const planId of ["evt-preview-digest", "evt-preview-missing"]) {
  test(`${planId} keeps the formatted preview read-only without a full Plan`, async ({ page, request }) => {
    await page.addInitScript(() => localStorage.setItem("zf.webActionToken", "plan-agent-fixture-token"));
    const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
    let contexts = 0;
    await page.route("**/plan-artifact-review", route => { contexts++; return route.continue(); });
    await page.goto(`/?project=${project}&page=inbox&plan=${planId}`);
    const panel = page.getByRole("dialog", { name: "Plan preview", exact: true });
    await panel.getByRole("button", { name: "引用编辑", exact: true }).click();
    await expect(panel.getByLabel("Plan review artifact")).toHaveValue("");
    const document = panel.getByTestId("plan-review-document");
    await expect(document.getByRole("heading").first()).toBeVisible();
    await expect(document).not.toContainText("END-OF-MATRIX");
    await expect(panel.getByRole("status").filter({ hasText: "当前未绑定完整 Plan 正文" })).toBeVisible();
    await panel.getByRole("button", { name: "Agent", exact: true }).click();
    await expect(panel.getByRole("dialog", { name: "Plan Agent", exact: true }).getByRole("textbox")).toBeDisabled();
    expect(contexts).toBe(0);
  });
}

for (const size of [{ width: 1440, height: 1000 }, { width: 1440, height: 1400 }, { width: 390, height: 844 }]) {
  test(`quoted editing fills the Preview reader at ${size.width}x${size.height}`, async ({ page, request }) => {
    await page.setViewportSize(size);
    await page.addInitScript(() => localStorage.setItem("zf.webActionToken", "plan-agent-fixture-token"));
    const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
    await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-complete`);
    const panel = page.getByRole("dialog", { name: "Plan preview", exact: true });
    await panel.getByRole("button", { name: "工程上下文", exact: true }).click();
    await panel.getByRole("button", { name: "完整原文", exact: true }).click();
    const reader = panel.locator(".plan-preview-reader");
    const original = (await reader.boundingBox())!;
    const originalDocument = (await panel.locator(".plan-preview-markdown-pane").boundingBox())!;
    await panel.getByRole("button", { name: "引用编辑", exact: true }).click();
    const document = panel.getByTestId("plan-review-document");
    await expect(document).toBeVisible();
    const editing = (await reader.boundingBox())!;
    expect(editing.x).toBe(0);
    expect(editing.width).toBe(size.width);
    expect(editing.y).toBe(original.y);
    expect(editing.y + editing.height).toBe(size.height);
    await expect(panel.locator(".plan-preview-context")).toBeHidden();
    const body = (await document.boundingBox())!;
    const bottomInset = await reader.evaluate(element => {
      const style = getComputedStyle(element);
      return parseFloat(style.paddingBottom) + parseFloat(style.borderBottomWidth);
    });
    expect(body.y + body.height).toBeCloseTo(editing.y + editing.height - bottomInset, 0);
    expect(body.height).toBeGreaterThanOrEqual(originalDocument.height - 40);
    expect(await reader.evaluate(element => element.scrollHeight - element.clientHeight)).toBeLessThanOrEqual(1);
    const toolbar = panel.getByRole("navigation", { name: "Plan document view" });
    const toolbarTop = (await toolbar.boundingBox())!.y;
    await document.evaluate(element => { element.scrollTop = element.scrollHeight; });
    await expect(document.getByText("END-OF-FULL-PLAN", { exact: true })).toBeInViewport();
    expect((await toolbar.boundingBox())!.y).toBeCloseTo(toolbarTop, 0);
    await toolbar.getByRole("button", { name: "Raw", exact: true }).click();
    await expect(panel.getByTestId("plan-review-source")).toContainText("END-OF-FULL-PLAN");
    expect((await document.boundingBox())!.height).toBeCloseTo(body.height, 0);
    await toolbar.getByRole("button", { name: "Agent", exact: true }).click();
    await expect(panel.getByRole("dialog", { name: "Plan Agent", exact: true })).toBeInViewport();
    expect((await document.boundingBox())!.height).toBeCloseTo(body.height, 0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await panel.screenshot({ path: test.info().outputPath("full-reader.png") });
    await panel.getByRole("button", { name: "Close agent chat", exact: true }).click();
    await panel.getByRole("button", { name: "返回审阅", exact: true }).click();
    await expect(panel.locator(".plan-preview-context")).toBeVisible();
    expect(await reader.boundingBox()).toEqual(original);
  });
}

test("quoted Preview preserves reading typography and formats a long table with source-bound quotes", async ({ page, request }) => {
  await page.addInitScript(() => localStorage.setItem("zf.webActionToken", "plan-agent-fixture-token"));
  const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
  const preview = await (await request.get(`/api/projects/${project}/plans/evt-preview-rich/preview`)).json();
  let discussions = 0;
  await page.route("**/plan-artifact-review", route => {
    const payload = route.request().postDataJSON();
    if (payload.operation === "context") return route.continue();
    expect(payload.operation).toBe("discuss");
    expect(Array.from(preview.markdown).slice(payload.quote.start, payload.quote.end).join("")).toBe(payload.quote.text);
    expect(payload.quote.text).toMatch(/^\| 条款 \| 行为 \|/);
    expect(payload.quote.text).toContain("R-2399");
    discussions++;
    return route.fulfill({ json: { ok: true, answer: "表格引用绑定正确。", draft_ref: null } });
  });
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-rich`);
  const panel = page.getByRole("dialog", { name: "Plan preview", exact: true });
  const heading = panel.getByRole("heading", { name: "格式化长计划", exact: true });
  await expect(heading).toBeVisible();
  const readingFont = await heading.evaluate(element => getComputedStyle(element).fontSize);
  await panel.getByRole("button", { name: "引用编辑", exact: true }).click();
  const document = panel.getByTestId("plan-review-document");
  await expect(document.getByRole("heading", { name: "格式化长计划", exact: true })).toHaveCSS("font-size", readingFont);
  await expect(document.locator("tbody tr")).toHaveCount(2400);
  await expect(document.locator(".agent-markdown-plain-guard")).toHaveCount(0);
  const bold = document.locator('[data-streamdown="strong"]');
  await expect(bold).toContainText(["原子提交"]);
  await expect(bold).toHaveCSS("font-weight", "700");
  const toolbar = panel.getByRole("navigation", { name: "Plan document view" });
  await expect(toolbar.getByRole("button", { name: "Preview", exact: true })).toHaveAttribute("aria-pressed", "true");
  await toolbar.getByRole("button", { name: "Raw", exact: true }).click();
  await expect(document.getByTestId("plan-review-source")).toHaveText(preview.markdown);
  await toolbar.getByRole("button", { name: "Preview", exact: true }).click();
  await expect(document.locator("tbody tr")).toHaveCount(2400);
  const lastCell = document.locator("tbody tr").last().locator("td").first();
  await lastCell.scrollIntoViewIfNeeded();
  await lastCell.dblclick({ position: { x: 12, y: 10 } });
  expect(await page.evaluate(() => window.getSelection()?.toString().trim())).toBeTruthy();
  await panel.getByRole("button", { name: "引用到 Agent", exact: true }).click();
  const agent = panel.getByRole("dialog", { name: "Plan Agent", exact: true });
  await agent.getByRole("textbox").fill("讨论这个表格。");
  await agent.getByRole("button", { name: "Send", exact: true }).click();
  await expect(agent).toContainText("表格引用绑定正确。");
  expect(discussions).toBe(1);
  await agent.getByRole("button", { name: "Close agent chat", exact: true }).click();
  await document.evaluate(element => { element.scrollTop = 0; });
  await panel.screenshot({ path: test.info().outputPath("formatted-quote-reader.png") });
});

test("Inbox Plan editing uses quoted headless chat and keeps approval locked through Apply", async ({ page, request }) => {
  await page.addInitScript(() => localStorage.setItem("zf.webActionToken", "plan-agent-fixture-token"));
  const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
  const planId = "evt-preview-complete";
  let status = "pending";
  let legacyChats = 0;
  let approvals = 0;
  let applied = 0;
  await page.route("**/actions/chat-orchestrator", route => { legacyChats++; return route.fulfill({ json: { ok: false } }); });
  await page.route("**/actions/plan-approve", route => { approvals++; return route.fulfill({ json: { ok: true } }); });
  await page.route(`**/plans/${planId}/preview`, async route => {
    const response = await route.fetch();
    await route.fulfill({ json: { ...await response.json(), status } });
  });
  await page.route("**/plan-artifact-review", async route => {
    const payload = route.request().postDataJSON();
    if (payload.operation === "context") {
      const response = await route.fetch();
      expect(response.ok(), await response.text()).toBe(true);
      return route.fulfill({ json: { ...await response.json(), editable: status === "pending" } });
    }
    if (payload.operation === "apply") {
      applied++; status = "rejected";
      return route.fulfill({ json: { ok: true, status: "replan_requested" } });
    }
    expect(payload.plan_id).toBe(planId);
    expect(payload.mode).toBe("chat");
    expect(payload.quote.text).toBe("API、业务、数据库的验证责任。");
    return route.fulfill({ json: { ok: true, answer: "仅修改引用段落。", before: payload.quote.text,
      after: "API、业务、数据库的验收责任。", draft_ref: { ref: "fixture-plan-review" } } });
  });
  await page.goto(`/?project=${project}&page=inbox&plan=${planId}`);
  const dialog = page.getByRole("dialog", { name: "Plan preview", exact: true });
  await dialog.getByRole("button", { name: "引用编辑", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "发送修改请求", exact: true })).toHaveCount(0);
  const toolbar = dialog.getByRole("navigation", { name: "Plan document view" });
  await expect(toolbar.locator(":scope > button")).toHaveText(["Preview", "Raw", "Agent"]);
  const documentTop = (await dialog.getByTestId("plan-review-document").boundingBox())!.y;
  const block = dialog.locator(".plan-review-block").filter({ hasText: "API、业务、数据库的验证责任。" });
  await expect(dialog.getByRole("button", { name: "引用编辑此段", exact: true })).toHaveCount(0);
  const quoteAction = dialog.getByRole("button", { name: "引用到 Agent", exact: true });
  await expect(quoteAction).toHaveCount(0);
  await block.locator("p").dblclick({ position: { x: 12, y: 10 } });
  expect(await page.evaluate(() => window.getSelection()?.toString().trim())).toBeTruthy();
  await quoteAction.click();
  const agent = dialog.getByRole("dialog", { name: "Plan Agent", exact: true });
  await expect(agent.getByRole("textbox")).toHaveCount(1);
  const toolbarBox = (await toolbar.boundingBox())!;
  const agentBox = (await agent.boundingBox())!;
  expect(agentBox.y).toBeGreaterThanOrEqual(toolbarBox.y + toolbarBox.height);
  expect(agentBox.y - toolbarBox.y - toolbarBox.height).toBeLessThan(16);
  expect(agentBox.x).toBeCloseTo(toolbarBox.x, 0);
  expect((await dialog.getByTestId("plan-review-document").boundingBox())!.y).toBeCloseTo(documentTop, 0);
  await agent.getByRole("button", { name: "Close agent chat", exact: true }).click();
  await toolbar.getByRole("button", { name: "Agent", exact: true }).click();
  await expect(agent.getByTestId("plan-review-quote")).toContainText("API、业务、数据库的验证责任。");
  expect((await agent.boundingBox())!.width).toBeLessThanOrEqual(440);
  await agent.getByRole("textbox").fill("将验证责任改为验收责任。");
  await agent.getByRole("button", { name: "Send", exact: true }).click();
  await expect(agent.getByTestId("plan-review-diff")).toContainText("验收责任");
  await expect(dialog.getByRole("button", { name: "Approve", exact: true })).toBeDisabled();
  await expect(dialog.getByRole("button", { name: "返回审阅", exact: true })).toBeDisabled();
  await expect(dialog.getByRole("button", { name: "Close preview", exact: true })).toBeDisabled();
  await block.locator("p").evaluate(element => {
    const range = document.createRange(); range.selectNodeContents(element);
    const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range);
  });
  await expect(quoteAction).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  const mobileToolbar = (await toolbar.boundingBox())!;
  const mobileAgent = (await agent.boundingBox())!;
  expect(mobileAgent.y - mobileToolbar.y - mobileToolbar.height).toBeLessThan(16);
  expect(mobileAgent.x + mobileAgent.width).toBeLessThanOrEqual(390);
  expect((await toolbar.getByRole("button", { name: "Agent", exact: true }).boundingBox())!.y)
    .toBeCloseTo((await toolbar.getByRole("button", { name: "Preview", exact: true }).boundingBox())!.y, 0);
  await agent.screenshot({ path: test.info().outputPath("inbox-agent-diff.png") });
  await agent.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(dialog.getByText("此版本已退回，等待新版 Plan Preview。", { exact: true }).first()).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Approve", exact: true })).toBeDisabled();
  await expect(dialog.getByRole("button", { name: "返回审阅", exact: true })).toBeEnabled();
  expect(applied).toBe(1); expect(legacyChats).toBe(0); expect(approvals).toBe(0);
});
