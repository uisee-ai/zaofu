import { test, expect } from "@playwright/test";

for (const action of ["replan-approve", "replan-reject"]) {
  test(`owner deep link waits for approval identity and sends ${action}`, async ({ page, request }) => {
    const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
    let resolvedPreviews = 0;
    let resolvedStatus = "";
    let releaseInbox!: () => void;
    const ready = new Promise<void>((resolve) => { releaseInbox = resolve; });
    await page.route("**/operator/inbox", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      const original = body.items.find((item: { plan_id?: string }) => item.plan_id === "evt-preview-rich");
      body.items = [{ ...original, id: "approval:owner-review", kind: "approval",
        owner_route: "plan_synth_owner_decision", approval_ref: "owner-review",
        proposal_ref: "proposal.json", eval_ref: "evaluation.json" }];
      await ready;
      await route.fulfill({ response, json: body });
    });
    await page.route("**/plans/evt-preview-rich/preview/summary", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      if (resolvedStatus) { body.status = resolvedStatus; resolvedPreviews += 1; }
      body.actions = { ...body.actions, approve: "replan-approve", reject: "replan-reject" };
      await route.fulfill({ response, json: body });
    });
    await page.route("**/plans/evt-preview-rich/preview", async route => {
      const response = await route.fetch();
      const body = await response.json();
      if (resolvedStatus) body.status = resolvedStatus;
      await route.fulfill({ response, json: body });
    });
    const actions: { name: string; payload: Record<string, unknown> }[] = [];
    await page.route("**/actions/*", async (route) => {
      const name = route.request().url().split("/").pop()!;
      if (name !== "inbox-item-read") {
        actions.push({ name, payload: route.request().postDataJSON().payload });
        resolvedStatus = name === "replan-approve" ? "approved" : "rejected";
      }
      await route.fulfill({ json: { ok: true } });
    });
    await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-rich`);
    const dialog = page.getByRole("dialog", { name: "Plan preview" });
    const approve = dialog.getByRole("button", { name: "Approve decisions", exact: true });
    const reject = dialog.getByRole("button", { name: "Reject", exact: true });
    await expect(approve).toBeVisible();
    await dialog.getByPlaceholder("reject reason", { exact: true }).fill("revise scope");
    await expect(approve).toBeDisabled();
    await expect(reject).toBeDisabled();
    releaseInbox();
    const button = action === "replan-approve" ? approve : reject;
    await expect(button).toBeEnabled();
    await button.click();
    await expect.poll(() => actions.length).toBe(1);
    expect(actions[0].name).toBe(action);
    expect(actions[0].payload).toMatchObject({ approval_ref: "owner-review", proposal_ref: "proposal.json", eval_ref: "evaluation.json" });
    await expect.poll(() => resolvedPreviews, { timeout: 5000 }).toBeGreaterThanOrEqual(1);
    await expect(approve).toBeDisabled();
    await expect(reject).toBeDisabled();
    await page.unrouteAll({ behavior: "wait" });
  });
}

// Uses tests.plan_preview_app's actual FastAPI + React app, not page.setContent.
test("complete Plan, bound matrix and summary remain distinct without writes", async ({ page, request }) => {
  const snapshot = await (await request.get("/api/snapshot")).json();
  const project = snapshot.project.project_id;
  const mutations: string[] = [];
  page.on("request", (req) => {
    if (req.method() !== "GET" && req.url().includes("/api/") && !req.url().endsWith("/actions/inbox-item-read")) mutations.push(req.url());
  });
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-complete`);
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  await dialog.getByRole("button", { name: "工程上下文", exact: true }).click();
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
  await page.route("**/plans/evt-preview-complete/preview/summary", async (route) => {
    const response = await route.fetch();
    await delayed;
    await route.fulfill({ response });
    completed();
  });
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-complete`);
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  await expect(dialog.getByText("Loading...", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "Close preview" }).click();
  const next = page.getByTestId("inbox-queue-item").filter({ hasText: "evt-preview-digest" });
  await next.click();
  await page.locator(".inbox-reading-pane").getByRole("button", { name: "Preview", exact: true }).click();
  await expect(dialog.getByRole("status")).toContainText("仅有计划摘要");
  const lateResponse = page.waitForResponse("**/plans/evt-preview-complete/preview/summary");
  release();
  await fulfilled;
  await (await lateResponse).finished();
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(dialog.getByRole("status")).toContainText("仅有计划摘要");
  await expect(dialog.locator(".plan-preview-toolbar").getByText("evt-preview-digest", { exact: true })).toBeVisible();
  await expect(dialog.getByText("完整规划示例", { exact: true })).toHaveCount(0);
});

for (const openAnother of [false, true]) {
test(`a delayed post-approval refresh cannot ${openAnother ? "replace another Plan" : "reopen a closed Plan"}`, async ({ page, request }, info) => {
  const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
  let calls = 0;
  let release!: () => void;
  let refreshStarted!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  const started = new Promise<void>((resolve) => { refreshStarted = resolve; });
  await page.route("**/plans/evt-preview-complete/preview/summary", async (route) => {
    const response = await route.fetch();
    calls += 1;
    if (calls > 1) {
      refreshStarted();
      await delayed;
    }
    await route.fulfill({ response });
  });
  const actions: Record<string, unknown>[] = [];
  await page.route("**/actions/*", async (route) => {
    if (route.request().url().endsWith("/plan-approve")) actions.push(route.request().postDataJSON().payload);
    await route.fulfill({ json: { ok: true } });
  });
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-complete`);
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  await dialog.getByRole("button", { name: "Approve", exact: true }).click();
  await started;
  const busyDuringRefresh = await dialog.getByRole("button", { name: "Approve", exact: true }).isDisabled();
  await dialog.getByRole("button", { name: "Close preview" }).click();
  if (openAnother) {
    await page.getByTestId("inbox-queue-item").filter({ hasText: "evt-preview-digest" }).click();
    await page.locator(".inbox-reading-pane").getByRole("button", { name: "Preview", exact: true }).click();
    await expect(dialog.getByRole("status")).toContainText("仅有计划摘要");
  }
  const lateResponse = page.waitForResponse("**/plans/evt-preview-complete/preview/summary");
  release();
  await (await lateResponse).finished();
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  if (openAnother) {
    await expect(dialog.locator(".plan-preview-toolbar").getByText("evt-preview-digest", { exact: true })).toBeVisible();
  } else {
    await expect(dialog).toHaveCount(0);
  }
  expect(actions).toHaveLength(1);
  expect(actions[0]).toMatchObject({ plan_id: "evt-preview-complete" });
  expect(actions[0].request_event_id).toBeTruthy();
  expect(busyDuringRefresh).toBe(true);
  await page.screenshot({ path: info.outputPath('after-late-refresh.png') });
});
}

test("large Plan pagination preserves every character through the final page", async ({ page, request }) => {
  const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
  const preview = await (await request.get(`/api/projects/${project}/plans/evt-preview-long/preview`)).json();
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-long`);
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  const pagination = dialog.getByRole("navigation", { name: "计划阅读导航" });
  await expect(pagination).toBeVisible();
  await pagination.getByTitle("切换到章节分页").click();
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
  await expect(dialog.locator(".plan-preview-markdown-pane > section table").first()).toBeVisible();
  await dialog.getByTitle("切换到章节分页").click();
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
  const reviewDownloadEvent = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "下载审核包" }).click();
  const reviewDownload = await reviewDownloadEvent;
  expect(reviewDownload.suggestedFilename()).toBe("plan-review.md");
  const reviewStream = await reviewDownload.createReadStream();
  if (!reviewStream) throw new Error("review download stream missing");
  const reviewChunks: Buffer[] = [];
  for await (const chunk of reviewStream) reviewChunks.push(Buffer.from(chunk));
  expect(Buffer.concat(reviewChunks).toString("utf8")).toBe(
    `${preview.review_dossier_markdown.trimEnd()}\n\n---\n\n${preview.markdown}`,
  );
});

test("continuous large Plan exposes deferred content and can load it without losing the tail", async ({ page, request }, testInfo) => {
  // Exercise the accessible manual fallback independently of scroll observation.
  await page.addInitScript(() => Object.defineProperty(window, "IntersectionObserver", { value: undefined }));
  const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-long`);
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  const more = dialog.getByRole("button", { name: /继续加载正文/ });
  await expect(more).toBeAttached();
  await expect(dialog.getByRole("button", { name: "查看源码", exact: true })).toBeEnabled();
  const pane = dialog.getByRole("region", { name: "计划正文", exact: true });
  for (let step = 0; step < 100 && await more.count(); step += 1) {
    const before = await pane.locator(":scope > section").count();
    await more.click();
    await expect.poll(() => pane.locator(":scope > section").count()).toBeGreaterThan(before);
  }
  await expect(more).toHaveCount(0);
  await expect(pane).toContainText("END-OF-LONG-PLAN");
  await page.screenshot({ path: testInfo.outputPath("continuous-plan-tail.png") });
});

test("source deep link reads chapters without fetching whole Plan or unrelated task bodies", async ({ page, request }) => {
  const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
  const unrelated: string[] = [];
  page.on("request", req => {
    if (/\/preview$/.test(req.url()) || /\/artifacts\/(task_map|execution_spec|source_inventory)$/.test(req.url())) unrelated.push(req.url());
  });
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-chapters#plan=evt-preview-chapters&view=sources`);
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  await expect(dialog.getByRole("combobox", { name: "同版来源文档" })).toBeVisible();
  await expect(dialog.getByRole("navigation", { name: "来源阅读导航" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Approve", exact: true })).toBeEnabled();
  expect(unrelated).toEqual([]);
});

test("slow full review does not block the approval toolbar or module view", async ({ page, request }) => {
  const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
  let release!: () => void;
  let started!: () => void;
  const delay = new Promise<void>(resolve => { release = resolve; });
  const loading = new Promise<void>(resolve => { started = resolve; });
  let wholeReads = 0;
  await page.route("**/plans/evt-preview-review/preview", async route => {
    wholeReads += 1; started(); await delay; await route.continue();
  });
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-review`);
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  await expect(dialog.getByRole("heading", { name: "产品目标" })).toBeVisible();
  expect(wholeReads).toBe(0);
  await dialog.getByRole("button", { name: "完整原文", exact: true }).click();
  await loading;
  await expect(dialog.getByRole("button", { name: "Approve", exact: true })).toBeEnabled();
  await dialog.getByRole("button", { name: "模块规格", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "使用者与字段" })).toBeVisible();
  release();
  await page.unrouteAll({ behavior: "wait" });
});

for (const outcome of ["approved", "deleted"]) {
  test(`closed SSE reconnect reconciles an open Plan that became ${outcome}`, async ({ page, request }) => {
    const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
    let changed = false;
    let releaseStream!: () => void;
    const reconnect = new Promise<void>(resolve => { releaseStream = resolve; });
    let connections = 0;
    await page.route("**/api/projects/*/stream?*", async route => {
      if (connections++ === 0) {
        await route.fulfill({ contentType: "text/event-stream", body: ": connected\nretry: 100\n\n" });
      } else {
        await reconnect;
        await route.continue();
      }
    });
    await page.route("**/plans/evt-preview-rich/preview/summary", async route => {
      if (changed && outcome === "deleted") {
        await route.fulfill({ status: 404, json: { detail: "plan not found" } }); return;
      }
      const response = await route.fetch();
      const body = await response.json();
      if (changed) { body.status = "approved"; body.resolved_event_id = "resolved-during-disconnect"; }
      await route.fulfill({ response, json: body });
    });
    await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-rich`);
    const dialog = page.getByRole("dialog", { name: "Plan preview" });
    await expect(dialog.getByRole("button", { name: "Approve", exact: true })).toBeEnabled();
    await expect(page.locator(".status-reconnecting")).toBeVisible({ timeout: 10000 });
    changed = true;
    releaseStream();
    if (outcome === "approved") {
      await expect(dialog.getByRole("button", { name: "Approve", exact: true })).toBeDisabled({ timeout: 10000 });
    } else {
      await expect(dialog).toHaveCount(0, { timeout: 10000 });
      await expect(page.getByText(/预览已失效，请重新打开/)).toBeVisible();
    }
  });
}
