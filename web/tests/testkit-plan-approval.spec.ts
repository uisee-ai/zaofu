import { expect, test } from "@playwright/test";
import { writeFileSync } from "node:fs";

test.skip(process.env.ZF_TESTKIT_BROWSER_RUN !== "1", "explicit TestKit Docker tier");

test("raw plan approval reaches a real candidate and the completed board", async ({ page }) => {
  const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST!;
  const planId = process.env.ZF_TESTKIT_PLAN_ID!;
  const projectId = process.env.ZF_TESTKIT_PROJECT_ID!;
  const pageErrors: string[] = [];
  const serverErrors: string[] = [];
  const evidence: Record<string, unknown> = { plan_id: planId };
  page.on("pageerror", error => pageErrors.push(String(error)));
  page.on("response", response => {
    if (response.status() >= 500) serverErrors.push(`${response.status()} ${response.url()}`);
  });
  await page.addInitScript((actionToken: string) => {
    localStorage.clear();
    localStorage.setItem("zf.webActionToken", actionToken);
    localStorage.setItem("zf.themeMode", "light");
  }, token);

  try {
    await page.goto(`/?page=inbox&project=${encodeURIComponent(projectId)}`, { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("plan-approval-panel")).toBeVisible();
    const card = page.getByTestId("approval-card");
    await expect(card).toBeVisible();
    await expect(card).toContainText("Plan Ready");
    await expect(card).toContainText(planId);

    await card.getByRole("button", { name: "Preview" }).click();
    const dialog = page.getByRole("dialog", { name: "Plan preview" });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(planId);
    await expect(dialog).toContainText("TASK-BLOCKING");
    await expect(dialog).toContainText("git cat-file -e HEAD:README.md");
    await page.screenshot({ path: "/zf-run/before.png" });

    const approval = page.waitForResponse((response) => (
      response.url().endsWith("/actions/plan-approve")
      && response.request().method() === "POST"
    ));
    await dialog.getByRole("button", { name: "Approve" }).click();
    const response = await approval;
    expect(response.status()).toBe(200);
    evidence.request = response.request().postDataJSON();
    evidence.action = await response.json();
    expect(evidence.action).toEqual(expect.objectContaining({ ok: true, plan_id: planId }));

    await expect(dialog).toContainText("approved");
    await expect(page.getByText("No inbox items in this view.")).toBeVisible();
    const inbox = await page.request.get(`/api/projects/${projectId}/operator/inbox`);
    expect(inbox.status()).toBe(200);
    const body = await inbox.json() as { items: Array<{ plan_id?: string; status: string }> };
    expect(body.items).toEqual(expect.arrayContaining([
      expect.objectContaining({ plan_id: planId, status: "approved" }),
    ]));
    await page.getByRole("button", { name: "Close preview" }).click();
    await page.goto(`/?page=board&project=${encodeURIComponent(projectId)}`, { waitUntil: "domcontentloaded" });
    const task = page.locator('.task-card[data-task-id="TASK-BLOCKING"]');
    await expect(task).toHaveAttribute("data-task-status", "done", { timeout: 30_000 });
    evidence.dom_status = await task.getAttribute("data-task-status");
    await page.screenshot({ path: "/zf-run/after.png" });
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(task).toHaveAttribute("data-task-status", "done");
    evidence.reload_status = await task.getAttribute("data-task-status");
    const detail = await page.request.get(`/api/projects/${projectId}/tasks/TASK-BLOCKING`);
    expect(detail.status()).toBe(200);
    expect((await detail.json()).task.status).toBe("done");
    expect(pageErrors).toEqual([]);
    expect(serverErrors).toEqual([]);
  } finally {
    writeFileSync("/zf-run/browser-truth.json", JSON.stringify({
      ...evidence, page_errors: pageErrors, server_errors: serverErrors,
    }, null, 2));
  }
});
