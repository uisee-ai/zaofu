import { test, expect } from "@playwright/test";

for (const width of [1440, 390]) {
  test(`${width}px material catalog loads metadata then only the selected source`, async ({ page, request }, info) => {
    await page.setViewportSize({ width, height: 900 });
    const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
    const wholeBodies: string[] = [];
    page.on("request", req => {
      if (/\/preview$/.test(req.url()) || /\/artifacts\/(task_map|execution_spec|source_inventory)$/.test(req.url())) wholeBodies.push(req.url());
    });
    await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-review#plan=evt-preview-review&view=materials`);
    const dialog = page.getByRole("dialog", { name: "Plan preview" });
    const catalog = dialog.getByRole("region", { name: "材料目录", exact: true });
    await expect(catalog.getByText("这里只展示本版绑定的材料与当前文件状态；正文尚未校验，目录完整不代表实现或验收完成。", { exact: true })).toBeVisible();
    const source = catalog.locator("article").filter({ hasText: "artifacts/source.md" });
    await expect(source).toHaveCount(1);
    await expect(source).toContainText("text/markdown");
    await expect(source).toContainText("正文未校验");
    await expect(source).toContainText("bytes");
    await expect(source).toContainText("替代关系：未声明");
    const overflows = await catalog.evaluate(el => el.scrollWidth > el.clientWidth + 1);
    expect(overflows).toBe(false);
    expect(wholeBodies).toEqual([]);
    await source.getByRole("button", { name: "按章节阅读", exact: true }).click();
    await expect(dialog.getByRole("heading", { name: "原始产品需求", exact: true })).toBeVisible();
    expect(wholeBodies).toEqual([]);
    await page.screenshot({ path: info.outputPath("selected-source.png") });
  });
}
