import { expect, test } from "@playwright/test";

// This spec uses the real isolated API, event writer and projection, with no route interception.
// Fixture: tests/e2e/channel_collaboration_browser_fixture.py (8002+, never production).
test("Owner defers a question through the real API and keeps its decision after reload", async ({ page, request }) => {
  test.skip(process.env.ZF_COLLABORATION_BROWSER_FIXTURE !== "1", "requires isolated collaboration fixture");
  await page.addInitScript(() => localStorage.setItem("zf.webActionToken", "zf-isolated-collaboration-browser"));
  const workspace = await (await request.get("/api/workspace/projects")).json();
  const project = String(workspace.active_project_id);
  const api = `/api/projects/${encodeURIComponent(project)}/channels/ch-collaboration`;
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`/?project=${encodeURIComponent(project)}&page=channels&channel=ch-collaboration`);
  await expect(page.getByTestId("channel-flow-nav")).toBeVisible();
  await page.getByTestId("channel-flow-next").click();
  await page.getByRole("button", { name: /Choose databases.*Review question/ }).click();
  const card = page.locator('.channel-question-card[data-question-id="q-db"]');
  await expect(card).toBeVisible();
  if (!(await card.evaluate(node => (node as HTMLDetailsElement).open))) await card.locator("summary").click();
  const submitted = page.waitForResponse(response => response.url().endsWith("/actions/channel-question-resolve"));
  await card.getByRole("button", { name: "本轮暂缓", exact: true }).click();
  const response = await submitted;
  expect(response.status()).toBe(202);
  expect(response.request().postDataJSON().payload.resolution).toBe("deferred");
  await expect.poll(async () => {
    const detail = await (await request.get(api)).json();
    return Object.values(detail.open_questions || {}).find((row: any) => row.question_id === "q-db");
  }).toMatchObject({ status: "resolved", resolution: "deferred", resolved_by: "owner:operator" });
  await page.reload();
  await expect(card).toHaveCount(0);
  const history = page.getByTestId("channel-decision-history");
  await history.locator(":scope > summary").click();
  await expect(history.locator('[data-decision-category="deferred"]')).toContainText("本轮暂缓");
  await page.getByTestId("channel-flow-discussion").click();
  await expect(page.getByTestId("channel-discussion-activity")).toContainText("dba");
  await expect(page.getByRole("button", { name: "Restart discussion", exact: true })).toBeEnabled();
  const detail = await (await request.get(api)).json();
  const question: any = Object.values(detail.open_questions).find((row: any) => row.question_id === "q-db");
  expect(question.answer).toContain("不代表批准");
  expect(question.resolved_event_id).toBeTruthy();
  expect(detail.discussion_attention.main.missing_frozen_members).toContain("dba");
  expect(detail.discussion_attention.main.can_restart).toBe(true);
  expect(detail.workflow_requests).toEqual([]);
  expect(errors).toEqual([]);
});
