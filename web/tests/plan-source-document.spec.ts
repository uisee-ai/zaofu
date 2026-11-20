import { test, expect } from "@playwright/test";

async function openSource(page: import("@playwright/test").Page, request: import("@playwright/test").APIRequestContext, anchor = "") {
  const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
  await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-chapters`);
  const dialog = page.getByRole("dialog", { name: "Plan preview" });
  await dialog.getByRole("button", { name: "来源原文", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "来源示例" })).toBeVisible();
  if (anchor) {
    const url = new URL(page.url());
    const location = new URLSearchParams(url.hash.slice(1));
    location.set("anchor", anchor); url.hash = location.toString();
    await page.goto(url.toString());
  }
  return dialog;
}

test("bound source loads chapters and paged index, preserves final chapter and full download", async ({ page, request }, info) => {
  const raw: string[] = [];
  const reads: string[] = [];
  page.on("request", req => {
    const url = new URL(req.url());
    if (url.pathname.endsWith("/artifacts/requirement_source")) raw.push(url.pathname);
    if (url.pathname.includes("/document/sections/")) reads.push(url.pathname);
  });
  const dialog = await openSource(page, request);
  await expect(dialog.getByRole("region", { name: "来源正文" })).toContainText("开头可读");
  expect(raw).toEqual([]);
  expect(reads).toHaveLength(1);
  await dialog.locator("summary", { hasText: "章节索引" }).click();
  await expect(dialog.getByRole("combobox", { name: "来源章节" }).locator("option")).toHaveCount(100);
  await dialog.getByRole("button", { name: "后一组目录" }).click();
  await expect(dialog.getByRole("combobox", { name: "来源章节" }).locator("option")).toHaveCount(34);
  await dialog.getByRole("combobox", { name: "来源章节" }).selectOption("132");
  await expect(dialog.getByRole("heading", { name: "最后来源" })).toBeVisible();
  await expect(dialog.getByRole("region", { name: "来源正文" })).toContainText("END-OF-BOUND-SOURCE");
  await expect(page).toHaveURL(/anchor=section-133/);
  await page.reload();
  await expect(dialog.getByRole("heading", { name: "最后来源" })).toBeVisible();
  expect(raw).toEqual([]);
  const href = await dialog.getByRole("link", { name: "完整原文 / 下载" }).getAttribute("href");
  const full = await request.get(href!);
  expect(full.status()).toBe(200);
  expect(await full.text()).toContain("END-LONG-SOURCE");
  expect(await full.text()).toContain("END-OF-BOUND-SOURCE");
  await page.screenshot({ path: info.outputPath("final-chapter.png") });
});

test("long source exposes bounded UTF8 pages and reachable navigation on mobile", async ({ page, request }, info) => {
  const dialog = await openSource(page, request, "section-132");
  await page.setViewportSize({ width: 390, height: 844 });
  const nav = dialog.getByRole("navigation", { name: "章节片段导航" });
  await expect(nav).toContainText("1 / 3");
  const body = dialog.getByRole("region", { name: "来源正文" });
  const pieces = [await body.innerText()];
  for (const current of [2, 3]) {
    await nav.getByRole("button", { name: "下一片段" }).click();
    await expect(nav).toContainText(`${current} / 3`);
    pieces.push(await body.innerText());
  }
  expect(pieces.every(part => new TextEncoder().encode(part).length <= 65536)).toBe(true);
  expect(pieces.join("")).toContain("END-LONG-SOURCE");
  expect(pieces.join("")).not.toContain("�");
  await body.evaluate(el => { el.scrollTop = el.scrollHeight; });
  await expect(nav.getByRole("button", { name: "上一片段" })).toBeInViewport();
  expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: info.outputPath("mobile-long-source.png") });
});

test("late chapter response cannot replace a newer selection", async ({ page, request }) => {
  let release!: () => void;
  const delayed = new Promise<void>(resolve => { release = resolve; });
  const dialog = await openSource(page, request);
  await page.route("**/document/sections/1?*", async route => {
    const response = await route.fetch();
    await delayed;
    await route.fulfill({ response }).catch(() => {});
  });
  const requested = page.waitForRequest("**/document/sections/1?*");
  await dialog.getByRole("combobox", { name: "来源章节" }).selectOption("1");
  await requested;
  await dialog.getByRole("combobox", { name: "来源章节" }).selectOption("2");
  await expect(dialog.getByRole("heading", { name: "来源章节 1", exact: true })).toBeVisible();
  release();
  await expect(dialog.getByRole("heading", { name: "来源章节 0", exact: true })).toHaveCount(0);
  await expect(page).toHaveURL(/anchor=section-3/);
});

test("version conflicts replace old body with an explicit error", async ({ page, request }) => {
  const dialog = await openSource(page, request);
  await page.route("**/document/sections/1?*", route => route.fulfill({ status: 409, json: { detail: "source version changed" } }));
  await dialog.getByRole("combobox", { name: "来源章节" }).selectOption("1");
  await expect(dialog.getByRole("alert")).toContainText("HTTP 409");
  await expect(dialog.getByRole("heading", { name: "来源示例" })).toHaveCount(0);
});
