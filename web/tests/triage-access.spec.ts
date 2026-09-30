import { expect, test } from "@playwright/test";

test("a reporter cannot open Triage through a direct URL", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("zf.webActionToken", "reporter-token");
  });
  await page.route("**/api/web-session", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        mode: "token_required",
        unlocked: false,
        actions_enabled: true,
        can_view_issue_triage: false,
      }),
    });
  });
  const triageRequests: string[] = [];
  await page.route("**/api/projects/**/issue-triage**", async (route) => {
    triageRequests.push(new URL(route.request().url()).pathname);
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ ok: false, status: "developer_capability_required" }),
    });
  });

  await page.goto("/?page=issue-triage&project=test-f1c6f35c");
  await expect.poll(() => new URL(page.url()).searchParams.get("page"), { timeout: 10000 }).toBe("board");
  expect(triageRequests).toEqual([]);
});
