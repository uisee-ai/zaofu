import { expect, test } from "@playwright/test";

// Isolated real API seeded with tests/test_channel_question_provenance.py::seed_question.
// ch-page additionally has a newer pm reply; the optional archived channel is read-only.
const project = process.env.ZF_QUESTION_PROVENANCE_PROJECT;
const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST;
const archivedChannel = process.env.ZF_QUESTION_PROVENANCE_ARCHIVED_CHANNEL;
test.skip(!project || !token, "Requires the isolated question provenance API fixture");
test.beforeEach(async ({ page }) => {
  await page.addInitScript((value) => localStorage.setItem("zf.webActionToken", value), token!);
});
const url = (channel: string) => `/?project=${project}&page=channels&channel=${channel}`;

test("recovered question targets the originating reply and retains source after an Owner answer", async ({ page, request }) => {
  const api = `/api/projects/${project}/channels/ch-source/conversation`;
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(url("ch-source"));
  const queue = page.getByTestId("channel-decision-queue");
  const original = page.locator(".agent-run-block").filter({ hasText: "Reply from synthesis" });
  const other = page.locator(".agent-run-block").filter({ hasText: "Reply from contribution" });
  for (let pass = 0; pass < 2; pass++) {
    if (pass) await page.reload();
    await expect(queue).toContainText("Open source question");
    await queue.getByRole("button", { name: /Which retention clock/ }).click();
    await expect(original.locator('.channel-question-card[data-question-id="q-owner"]')).toBeVisible();
    await expect(other.locator('.channel-question-card[data-question-id="q-owner"]')).toHaveCount(0);
    await expect(page.getByTestId("channel-question-source-diagnostics")).toHaveCount(0);
  }
  const question = original.locator('.channel-question-card[data-question-id="q-owner"]');
  await question.locator("summary").click();
  await question.getByRole("textbox").fill("Event time — isolated test decision");
  const answered = page.waitForResponse((response) => response.url().endsWith("/actions/channel-question-resolve"));
  await question.getByTestId("ask-user-submit").click();
  expect((await answered).status()).toBe(202);
  await expect(question).toHaveCount(0);
  await page.reload();
  const data = await (await request.get(api)).json();
  expect(data.open_questions[0]).toMatchObject({ status: "resolved", source_run_id: "run-synthesis",
    source_message_id: "input-synthesis", answer: "Event time — isolated test decision" });
  const history = page.getByTestId("channel-decision-history");
  await history.locator("summary").first().click();
  await history.getByTestId("channel-decision-group").locator("summary").first().click();
  await history.getByTestId("channel-decision-record").locator("summary").click();
  await expect(history.getByLabel("原始记录 q-owner")).toContainText('"source_run_id": "run-synthesis"');
  expect(errors).toEqual([]);
});

test("paginated source stays unassigned until its exact reply is loaded", async ({ page }) => {
  await page.route("**/channels/ch-page/conversation**", (route) => {
    const target = new URL(route.request().url());
    target.searchParams.set("limit", "1");
    return route.continue({ url: target.toString() });
  });
  await page.goto(url("ch-page"));
  const queue = page.getByTestId("channel-decision-queue");
  await expect(queue).toContainText("source unavailable");
  await queue.getByRole("button", { name: /Which retention clock/ }).click();
  const diagnostics = page.getByTestId("channel-question-source-diagnostics");
  await expect(diagnostics).toBeVisible();
  await expect(page.locator(".agent-run-block").filter({ hasText: "Reply from later" })
    .locator('.channel-question-card[data-question-id="q-owner"]')).toHaveCount(0);
  for (let count = 0; count < 4 && await diagnostics.isVisible(); count++) {
    const loaded = page.waitForResponse((response) => response.url().includes("/ch-page/conversation")
      && response.url().includes("before="));
    await diagnostics.getByRole("button", { name: "Load earlier source" }).click();
    await loaded;
    await expect(page.getByTestId("channel-load-earlier")).toBeEnabled();
  }
  await expect(page.locator(".agent-run-block").filter({ hasText: "Reply from synthesis" })
    .locator('.channel-question-card[data-question-id="q-owner"]')).toBeVisible();
  await expect(diagnostics).toHaveCount(0);
});

test("archived real synthesis questions recover their shared source after reload", async ({ page, request }, info) => {
  test.skip(!archivedChannel, "Optional archived real-provider evidence");
  const api = `/api/projects/${project}/channels/${archivedChannel}/conversation`;
  const data = await (await request.get(api)).json();
  const questions = data.open_questions.filter((q: { status: string }) => q.status === "open");
  expect(questions).toHaveLength(3);
  expect(new Set(questions.map((q: { source_run_id: string }) => q.source_run_id)))
    .toEqual(new Set(["run-6a710ab6eabe2c4b"]));
  await page.goto(url(archivedChannel!));
  for (let pass = 0; pass < 2; pass++) {
    if (pass) await page.reload();
    const queue = page.getByTestId("channel-decision-queue");
    await expect(queue.locator(".channel-decision-queue-item")).toHaveCount(3);
    await expect(queue).not.toContainText("source unavailable");
    for (const question of questions) {
      await queue.locator(`[data-question-id="${question.question_id}"]`).click();
      await expect(page.locator(`.agent-run-block[data-member-id="product_pm"] .channel-question-card[data-question-id="${question.question_id}"]`)).toBeVisible();
    }
    await expect(page.locator(".agent-run-block").filter({ has: page.locator(".channel-question-card") })).toHaveCount(1);
    await page.screenshot({ path: info.outputPath(`recovered-real-questions-${pass}.png`) });
  }
});
