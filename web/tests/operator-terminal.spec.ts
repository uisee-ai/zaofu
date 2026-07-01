import { expect, test } from "@playwright/test";

test("Kanban Agent no longer exposes an Advanced Terminal surface", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".status-pill.status-live"))
    .toBeVisible({ timeout: 90_000 });

  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(page.getByRole("dialog", { name: "Kanban Agent" })).toBeVisible();
  await expect(page.locator(".headless-input")).toBeEditable();
  await expect(page.locator(".terminal-fallback")).toHaveCount(0);
  await expect(page.getByLabel("Operator xterm")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Start", exact: true })).toHaveCount(0);
});
