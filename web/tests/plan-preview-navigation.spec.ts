import { expect, test } from "@playwright/test";

// Real API + React fixture: tests.plan_preview_app (same as plan-preview-content).
for (const viewport of [
  { width: 1485, height: 900 },
  { width: 390, height: 844 },
  { width: 320, height: 760 },
]) {
  test(`${viewport.width}px Plan navigation stays separate from scrolling text`, async ({ page, request }) => {
    await page.setViewportSize(viewport);
    const project = (await (await request.get("/api/snapshot")).json()).project.project_id;
    const writes: string[] = [];
    page.on("request", (req) => {
      if (req.method() !== "GET" && req.url().includes("/api/")) writes.push(req.url());
    });
    await page.goto(`/?project=${project}&page=inbox&plan=evt-preview-rich`);
    const dialog = page.getByRole("dialog", { name: "Plan preview" });
    const nav = dialog.getByRole("navigation", { name: "计划阅读导航" });
    const pane = dialog.locator(".plan-preview-markdown-pane");
    const previous = nav.getByRole("button", { name: "上一页" });
    const next = nav.getByRole("button", { name: "下一页" });
    await expect(nav).toBeVisible();
    await expect(previous).toBeDisabled();
    const initial = (await nav.boundingBox())!;

    // A visible toolbar alone does not prove that it stops covering the text.
    for (const position of [0, 700, Number.MAX_SAFE_INTEGER]) {
      await pane.evaluate((el, top) => { el.scrollTop = top; }, position);
      const navigation = (await nav.boundingBox())!;
      const content = (await pane.boundingBox())!;
      expect(navigation.y).toBeCloseTo(initial.y, 0);
      expect(content.y, "navigation must have its own space above the text viewport")
        .toBeGreaterThanOrEqual(navigation.y + navigation.height - 1);
      expect(content.height).toBeGreaterThan(120);
      for (const control of await nav.locator("button, select").all()) {
        const box = (await control.boundingBox())!;
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
      }
      const back = (await previous.boundingBox())!;
      const forward = (await next.boundingBox())!;
      expect(back.y, "previous/next controls stay on the same row").toBeCloseTo(forward.y, 0);
    }

    await next.click();
    await expect(nav).toContainText("第 2 /");
    await expect.poll(() => pane.evaluate((el) => el.scrollTop)).toBe(0);
    await expect(previous).toBeEnabled();
    await pane.focus();
    await page.keyboard.press("PageDown");
    await expect.poll(() => pane.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
    await previous.click();
    await expect(nav).toContainText("第 1 /");
    await expect.poll(() => pane.evaluate((el) => el.scrollTop)).toBe(0);

    const chapters = nav.getByRole("combobox", { name: "计划章节" });
    await chapters.selectOption(String((await chapters.locator("option").count()) - 1));
    await expect(next).toBeDisabled();
    await pane.evaluate((el) => { el.scrollTop = el.scrollHeight; });
    const tail = (await dialog.getByText("END-OF-RICH-PLAN", { exact: true }).boundingBox())!;
    const content = (await pane.boundingBox())!;
    expect(tail.y).toBeGreaterThanOrEqual(content.y);
    expect(tail.y + tail.height).toBeLessThanOrEqual(content.y + content.height);
    expect(writes).toEqual([]);
  });
}
