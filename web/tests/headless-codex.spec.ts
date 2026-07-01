import { expect, test } from "@playwright/test";

const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";

test.skip(!process.env.ZF_E2E_CODEX_HEADLESS, "real Codex headless smoke is opt-in");

test("Codex headless Kanban Agent replies through the hidden thread", async ({ page }) => {
  test.setTimeout(180_000);

  await page.addInitScript((value) => {
    if (value) window.localStorage.setItem("zf.webActionToken", value);
    window.localStorage.setItem("zf.operatorBackend", "codex-headless");
  }, token);

  await page.goto("/");
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(page.getByRole("dialog", { name: "Kanban Agent" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: /Agent backend: Codex/ })).toBeVisible();

  const input = page.locator(".headless-input");
  await expect(input).toBeVisible();
  await input.fill("Reply with exactly ZF_CODEX_HEADLESS_OK. Do not modify files.");
  await page.getByRole("button", { name: "Send message" }).click();

  await expect(page.locator(".agent-text-part").filter({
    hasText: "ZF_CODEX_HEADLESS_OK",
  })).toBeVisible({ timeout: 150_000 });
});
