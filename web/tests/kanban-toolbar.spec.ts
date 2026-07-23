import { expect, test } from "@playwright/test";

test.describe.configure({ timeout: 90_000 });

test("Task Board toolbar has exclusive, reversible mouse selection", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/?page=board", { waitUntil: "domcontentloaded" });

  const toolbar = page.locator(".task-toolbar");
  await expect(toolbar).toBeVisible({ timeout: 60_000 });
  const view = toolbar.getByRole("group", { name: "Task view" });
  const focus = toolbar.getByRole("group", { name: "Task focus" });
  const board = view.getByRole("button", { name: "Board", exact: true });
  const list = view.getByRole("button", { name: "List", exact: true });
  const all = focus.getByRole("button", { name: "All", exact: true });
  const ready = focus.getByRole("button", { name: "Ready", exact: true });
  const blocked = focus.getByRole("button", { name: "Blocked", exact: true });
  const verify = focus.getByRole("button", { name: "Verify", exact: true });
  const status = toolbar.getByLabel("Task status filter");
  const signal = toolbar.getByLabel("Task signal filter");

  await expect(all).toHaveAttribute("aria-pressed", "true");
  await expect(status).toHaveValue("all");
  await expect(signal).toHaveValue("all");
  await expect(board).toHaveAttribute("aria-pressed", "true");
  await expect(list).toHaveAttribute("aria-pressed", "false");
  await list.click();
  await expect(list).toHaveAttribute("aria-pressed", "true");
  await expect(board).toHaveAttribute("aria-pressed", "false");
  await board.click();

  const idle = await ready.evaluate((element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return { background: style.backgroundColor, border: style.borderColor, height: rect.height, width: rect.width };
  });
  await ready.hover();
  const hovered = await ready.evaluate((element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return { background: style.backgroundColor, border: style.borderColor, height: rect.height, width: rect.width };
  });
  expect(`${hovered.background}/${hovered.border}`).not.toBe(`${idle.background}/${idle.border}`);
  expect(hovered.height).toBe(idle.height);
  expect(hovered.width).toBe(idle.width);

  await ready.click();
  await expect(ready).toHaveAttribute("aria-pressed", "true");
  await expect(status).toHaveValue("all");
  await expect(signal).toHaveValue("ready");
  await ready.click();
  await expect(all).toHaveAttribute("aria-pressed", "true");
  await expect(ready).toHaveAttribute("aria-pressed", "false");
  await expect(signal).toHaveValue("all");

  await ready.click();
  await verify.click();
  await expect(verify).toHaveAttribute("aria-pressed", "true");
  await expect(ready).toHaveAttribute("aria-pressed", "false");
  await expect(status).toHaveValue("testing");
  await expect(signal).toHaveValue("all");
  await blocked.click();
  await expect(blocked).toHaveAttribute("aria-pressed", "true");
  await expect(verify).toHaveAttribute("aria-pressed", "false");
  await expect(status).toHaveValue("all");
  await expect(signal).toHaveValue("blocked");

  await status.selectOption("testing");
  await expect(focus.locator('button[aria-pressed="true"]')).toHaveCount(0);
  await all.click();
  await expect(all).toHaveAttribute("aria-pressed", "true");
  await expect(status).toHaveValue("all");
  await expect(signal).toHaveValue("all");
  await page.screenshot({ path: testInfo.outputPath("kanban-toolbar-desktop.png"), fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(view).toBeVisible();
  await expect(focus).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: testInfo.outputPath("kanban-toolbar-mobile.png"), fullPage: true });
});
