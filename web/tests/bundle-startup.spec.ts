import { expect, test } from "@playwright/test";

// Run against `vite preview` to exercise emitted ESM chunks, not Vite's
// development module loader. All API responses are isolated from real state.
test("production chunks initialize the workspace without module errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const data = path === "/api/workspace/projects"
      ? { items: [], active_project_id: "" }
      : path === "/api/workspace/onboarding"
        ? { show_welcome: false, backends: [] }
        : path === "/api/web-session"
          ? { mode: "read_only", actions_enabled: false, unlocked: false }
          : { configured: false, verified: false };
    await route.fulfill({ json: data });
  });
  await page.goto("/?page=board");
  await expect(page.locator(".app-shell")).toBeVisible();
  await expect(page.locator(".board-panel").getByRole("button", { name: "Add Project", exact: true })).toBeVisible();
  await page.reload();
  await expect(page.locator(".app-shell")).toBeVisible();
  expect(errors).toEqual([]);
});
