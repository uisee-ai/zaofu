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

test("large Plan pagination preserves every character through the final page", async ({ page, request }) => {
  const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
  const preview = await (await request.get(`/api/projects/${project}/plans/evt-preview-long/preview`)).json();
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-long`);
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  const pagination = dialog.getByRole("navigation", { name: "计划阅读导航" });
  await expect(pagination).toBeVisible();
  await pagination.getByRole("button", { name: "查看源码" }).click();
  const next = pagination.getByRole("button", { name: "下一页" });
  let reconstructed = "";
  let pages = 0;
  do {
    if (pages) await next.click();
    await expect(pagination).toContainText(`第 ${pages + 1} /`);
    reconstructed += await dialog.locator(".plan-document-source").textContent();
    pages += 1;
  } while (await next.isEnabled());
  expect(pages).toBeGreaterThan(1);
  expect(reconstructed).toBe(preview.markdown);
  expect(reconstructed).toContain("END-OF-LONG-PLAN");
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    const visible = await dialog.locator(".plan-preview-markdown-pane").evaluate((pane) => {
      pane.scrollTop = pane.scrollHeight;
      const text = pane.querySelector(".plan-document-source")!.firstChild!;
      const end = text.textContent!.trimEnd().length;
      const range = document.createRange();
      range.setStart(text, end - "END-OF-LONG-PLAN".length);
      range.setEnd(text, end);
      const tail = range.getBoundingClientRect();
      const bounds = pane.getBoundingClientRect();
      return tail.top >= bounds.top && tail.bottom <= bounds.bottom;
    });
    expect(visible, "scrolling the Plan pane must reveal the final text line").toBe(true);
  }
});

test("long Plan renders headings and tables instead of raw Markdown", async ({ page, request }) => {
  const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-rich`);
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  await expect(dialog.getByRole("heading", { name: "格式化长计划", exact: true })).toBeVisible();
  await expect(dialog.locator(".plan-preview-markdown table").first()).toBeVisible();
  let rows = 0;
  const next = dialog.getByRole("button", { name: "下一页" });
  let number = 1;
  do {
    await expect(dialog.getByRole("navigation", { name: "计划阅读导航" })).toContainText(`第 ${number} /`);
    rows += await dialog.locator(".plan-preview-markdown tbody tr").count();
    if (!(await next.isEnabled())) break;
    await next.click();
    number += 1;
  } while (true);
  expect(rows).toBe(2400);
  await expect(dialog.getByText("END-OF-RICH-PLAN", { exact: true })).toBeVisible();
  await expect(dialog.locator(".plan-preview-markdown .agent-code-block")).toContainText("node --test");
  const downloadEvent = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "下载 Markdown" }).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe("plan.md");
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  if (!stream) throw new Error("download stream missing");
  for await (const chunk of stream) chunks.push(Buffer.from(chunk));
  const downloaded = Buffer.concat(chunks).toString("utf8");
  const preview = await (await request.get(`/api/projects/${project}/plans/evt-preview-rich/preview`)).json();
  expect(downloaded).toBe(preview.markdown);
});
