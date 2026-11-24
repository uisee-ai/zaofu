import { expect, test } from "@playwright/test";


const projectId = process.env.ZF_NEEDS_INPUT_PROJECT_ID ?? "";
const actionToken = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";

test.describe.configure({ mode: "serial", timeout: 90_000 });

async function authorize(page: import("@playwright/test").Page) {
  await page.addInitScript(({ token }) => {
    window.localStorage.setItem("zf.webActionToken", token);
  }, { token: actionToken });
}

test("Channel decisions and Inbox links close through their source surfaces", async ({
  page,
  request,
}) => {
  expect(projectId).not.toBe("");
  expect(actionToken).not.toBe("");
  await authorize(page);

  await page.goto(
    `/?project=${encodeURIComponent(projectId)}&page=channels`
      + "&channel=ch-running-decisions",
  );
  const running = page.getByTestId("channel-flow-next");
  await expect(page.getByTestId("channel-discussion-attention")).toHaveCount(0);
  await expect(page.getByTestId("channel-header-members")).toBeVisible();
  await expect(page.getByTestId("channel-participant-rail")).toHaveCount(0);
  await expect(running).toContainText("Review");
  await expect(page.getByTestId("channel-owner-question-shelf")).toHaveCount(0);
  await running.click();
  await expect(page.locator(".channel-drawer")).toHaveCount(0);
  await expect(page.locator('[data-testid="channel-inline-question"], [data-testid="channel-unassigned-question-tray"]')).toBeVisible();
  await page.getByRole("button", { name: "Close question" }).click();

  await page.goto(
    `/?project=${encodeURIComponent(projectId)}&page=channels`
      + "&channel=ch-waiting-decisions",
  );
  const question = page.locator(
    '[data-testid="channel-inline-question"], [data-testid="channel-unassigned-question-tray"]',
  ).first();
  await expect(page.getByTestId("channel-discussion-attention")).toHaveCount(0);
  await expect(page.locator(".channel-owner-question-shelf")).toHaveCount(0);
  await expect(question).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(question).toBeVisible();
  const questionBounds = await question.boundingBox();
  expect(questionBounds).not.toBeNull();
  expect((questionBounds?.x ?? 0) + (questionBounds?.width ?? 0)).toBeLessThanOrEqual(390);
  await question.getByLabel("Developers").check();
  await question.getByTestId("ask-user-submit").click();
  await expect(page.locator('[data-testid="channel-inline-question"], [data-testid="channel-unassigned-question-tray"]')).toHaveCount(0);
  await expect(page.getByTestId("channel-discussion-attention")).toHaveCount(0);

  await expect.poll(async () => {
    const response = await request.get(
      `/api/projects/${encodeURIComponent(projectId)}/operator/inbox`,
    );
    const inbox = await response.json();
    return Number(inbox.summary?.decisions_pending ?? -1);
  }).toBe(2);

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(`/?project=${encodeURIComponent(projectId)}&page=inbox`);
  await expect(page.getByText("2 decisions", { exact: true })).toBeVisible();

  const channelDecision = page.locator(".operator-inbox-row").filter({
    hasText: "Which launch scope should the team optimize for?",
  });
  await channelDecision.click();
  await channelDecision.getByRole("link", { name: "Open channel" }).click();
  await expect(page).toHaveURL(/page=channels/);
  await expect(page).toHaveURL(/channel=ch-running-decisions/);
  await expect(page.getByTestId("channel-flow-next")).toBeVisible();

  await page.goto(`/?project=${encodeURIComponent(projectId)}&page=inbox`);
  const kanbanDecision = page.locator(".operator-inbox-row").filter({
    hasText: "Which workflow should execute this task?",
  });
  await kanbanDecision.click();
  await kanbanDecision.getByRole("link", { name: "Open zaofu" }).click();
  await expect(page).toHaveURL(/page=project/);
  await expect(page.getByRole("dialog", { name: "zaofu" })).toBeVisible();
});
