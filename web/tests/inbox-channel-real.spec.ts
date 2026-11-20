import { expect, test } from "@playwright/test";

test("Channel owner questions remain answerable outside Inbox", async ({ page, request }) => {
  test.setTimeout(60_000);
  test.skip(process.env.ZF_INBOX_BROWSER_FIXTURE !== "channel", "requires the isolated Channel fixture");
  await page.addInitScript(() => localStorage.setItem("zf.webActionToken", "zf-isolated-inbox-browser"));
  const workspace = await (await request.get("/api/workspace/projects")).json();
  const project = String(workspace.active_project_id);
  const inboxApi = `/api/projects/${encodeURIComponent(project)}/operator/inbox`;
  await page.goto(`/?project=${encodeURIComponent(project)}&page=inbox`);
  await expect(page.locator(".inbox-pending-count")).toHaveText("0");
  expect((await (await request.get(inboxApi)).json()).items).toHaveLength(0);
  await page.goto(`/?project=${encodeURIComponent(project)}&page=channels&channel=ch-collaboration&thread=main&question=q-db`);
  await expect(page).toHaveURL(/thread=main/);
  await expect(page).toHaveURL(/question=q-db/);
  const card = page.locator('.channel-question-card[data-question-id="q-db"]');
  await expect(card).toBeVisible();
  if (!(await card.evaluate(node => (node as HTMLDetailsElement).open))) await card.locator("summary").click();
  await card.getByRole("button", { name: "本轮暂缓", exact: true }).click();
  await expect.poll(async () => (await (await request.get(inboxApi)).json()).summary.decisions_pending).toBe(0);
  const inbox = await (await request.get(inboxApi)).json();
  expect(inbox.items).toHaveLength(0);
  const answers = await (await request.get(`/api/projects/${encodeURIComponent(project)}/events?type=channel.question.resolved`)).json();
  expect(answers.items).toHaveLength(1);
  const saved = await (await request.get(`/api/projects/${encodeURIComponent(project)}/events/${answers.items[0].id}`)).json();
  expect(saved.event.payload.question_id).toBe("q-db");
  expect(saved.event.payload.resolution).toBe("deferred");
  await expect(card).toHaveCount(0);
});
