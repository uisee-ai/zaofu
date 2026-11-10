import { test, expect } from "@playwright/test";
import shared from "./fixtures/spec206-shared.json" with { type: "json" };

for (const width of [1280, 375]) test(`${width}px real R2 shared case preserves task responsibility and raw decision states`, async ({ page, request }) => {
  await page.setViewportSize({ width, height: 900 });
  const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
  await page.route("**/api/web-session", route => route.fulfill({ json: { mode: "read_only", actions_enabled: false } }));
  await page.route("**/plans/evt-preview-review/preview", async route => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ response, json: { ...body, markdown: shared.markdown, review_dossier_markdown: "",
      artifacts: body.artifacts.filter((p: { name: string }) => ["task_map", "test_matrix", "source_index"].includes(p.name)),
      decision_review: { available: true, items: shared.task_map.owner_decision_items,
        counts: { total: 7, pending: 0, blocking: 0, follow_up: 0, resolved: 4, unknown: 3 }, error: "" },
    } });
  });
  for (const name of ["task_map", "test_matrix", "source_index"] as const) {
    await page.route(`**/plans/evt-preview-review/artifacts/${name}`, route => route.fulfill({ json: shared[name] }));
  }
  const writes: string[] = [], errors: string[] = [];
  page.on("request", r => { if (r.url().includes("/api/") && r.method() !== "GET") writes.push(r.url()); });
  page.on("pageerror", e => errors.push(e.message));
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-review`);
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  await dialog.getByRole("button", { name: "模块规格", exact: true }).click();
  await dialog.getByRole("combobox", { name: "审阅模块" }).selectOption("T-2");
  const repeated = dialog.locator("details").filter({ has: page.locator("summary").filter({ hasText: "共享重复章节：完整决定与命名场景绑定" }) });
  await expect(repeated).not.toHaveAttribute("open", "");
  await repeated.locator("summary").click();
  await expect(repeated).toContainText("S-add、S-invalid");
  await dialog.getByRole("button", { name: "完整原文", exact: true }).click();
  const download = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "下载 Markdown", exact: true }).click();
  const stream = await (await download).createReadStream(); const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  expect(Buffer.concat(chunks).toString()).toBe(shared.markdown);
  await dialog.getByRole("button", { name: "验收场景", exact: true }).click();
  await dialog.getByRole("combobox", { name: "审阅模块" }).selectOption("T-2");
  await expect(dialog.getByText("证据状态：NOT_RUN", { exact: true })).toBeVisible();
  await expect(dialog.getByText("场景命令未绑定到本任务验收项", { exact: true })).toHaveCount(0);
  await dialog.getByText("逐任务验收责任", { exact: true }).click();
  await expect(dialog.getByText("本片场景命令：V-CORE、V-BROWSER", { exact: true })).toBeVisible();
  await expect(dialog.getByText("本片场景命令：V-PRODUCT", { exact: true })).toBeVisible();
  await expect(dialog.getByText("V-PRODUCT · verify-lane-2 · e2e · 命令 AC：AC-I", { exact: true })).toBeVisible();
  expect(await dialog.locator(".plan-review-scroll").evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
  await dialog.getByRole("combobox", { name: "审阅模块" }).selectOption("T-3");
  await expect(dialog.getByText(/^本片责任待核对/)).toBeVisible();
  await expect(dialog.getByText("证据状态：NOT_RUN", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "待决事项", exact: true }).click();
  await expect(dialog.getByText(/^原始状态：deferred 1 · confirmed 4 · pending_validation 1 · proposed 1/)).toBeVisible();
  await expect(dialog.getByText(/已关闭 4 · 其他状态 3/)).toBeVisible();
  await dialog.locator("summary").filter({ hasText: "D-1 · deferred" }).click();
  await expect(dialog.getByText(/^最迟确认：未来版本立项前/)).toBeVisible();
  await page.unrouteAll({ behavior: "wait" });
  expect(writes).toEqual([]);
  expect(errors).toEqual([]);
});
