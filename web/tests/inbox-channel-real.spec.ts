import { expect, test } from "@playwright/test";

test("Inbox opens and resolves the exact Channel owner question", async ({ page, request }) => {
  test.setTimeout(60_000);
  test.skip(process.env.ZF_INBOX_BROWSER_FIXTURE !== "channel", "requires the isolated Channel fixture");
  await page.addInitScript(() => localStorage.setItem("zf.webActionToken", "zf-isolated-inbox-browser"));
  const workspace = await (await request.get("/api/workspace/projects")).json();
  const project = String(workspace.active_project_id);
  const inboxApi = `/api/projects/${encodeURIComponent(project)}/operator/inbox`;
  await page.goto(`/?project=${encodeURIComponent(project)}&page=inbox`);
  const row = page.locator('[data-inbox-id="channel-question:ch-collaboration:main:q-db"]');
  await expect(row).toBeVisible();
  await row.click();
  await expect.poll(async () => (await (await request.get(inboxApi)).json()).summary.unread).toBe(0);
  await page.locator(".inbox-reading-pane").getByRole("link", { name: "Open channel" }).click();
  await expect(page).toHaveURL(/thread=main/);
  await expect(page).toHaveURL(/question=q-db/);
  const card = page.locator('.channel-question-card[data-question-id="q-db"]');
  await expect(card).toBeVisible();
  if (!(await card.evaluate(node => (node as HTMLDetailsElement).open))) await card.locator("summary").click();
  await card.getByRole("button", { name: "本轮暂缓", exact: true }).click();
  await expect.poll(async () => (await (await request.get(inboxApi)).json()).summary.decisions_pending).toBe(0);
  const inbox = await (await request.get(inboxApi)).json();
  expect(inbox.items.find((item: { id: string }) => item.id.endsWith(":q-db")).status).toBe("resolved");
});
