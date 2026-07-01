import { expect, test } from "@playwright/test";

const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";

test.describe.configure({ timeout: 120_000 });

test("Kanban Agent defaults to hidden headless thread even with legacy terminal preference", async ({ page }) => {
  await page.addInitScript((value) => {
    if (value) window.localStorage.setItem("zf.webActionToken", value);
    window.localStorage.setItem("zf.operatorBackend", "deterministic");
  }, token);

  await page.goto("/");
  await expect(page.locator(".status-pill.status-live"))
    .toBeVisible({ timeout: 90_000 });
  await expect(page.getByRole("region", { name: "Kanban Agent collapsed" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Kanban Agent" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Open Kanban Agent" })).toBeVisible();
  await expect(page.getByLabel("Operator xterm")).toHaveCount(0);
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(page.getByRole("dialog", { name: "Kanban Agent" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Open Kanban Agent" })).toHaveCount(0);
  const backendTrigger = page.getByRole("button", { name: /Agent backend/ });
  await expect(backendTrigger).toBeVisible();
  await expect(backendTrigger).toContainText(/Claude|Codex/);
  await expect(backendTrigger).not.toContainText("headless");
  await expect(page.locator(".orchestrator-panel .chat-surface-head")).toHaveCount(0);
  await expect(page.locator(".headless-input")).toBeVisible();
  await expect(page.getByLabel("Operator xterm")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Start", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Stop", exact: true })).toHaveCount(0);

  await expect(page.getByRole("button", { name: "Chat", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Agent transcript")).toHaveCount(0);
  await expect(page.getByPlaceholder("Ask Kanban Agent to create, move, inspect, or run tasks")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Create Task" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Request Fanout" })).toHaveCount(0);
});
