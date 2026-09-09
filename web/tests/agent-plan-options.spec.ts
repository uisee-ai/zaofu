import { expect, test } from "@playwright/test";

const CASES = [
  ["fx-task-plan-options", "Full delivery Task (Recommended)"],
  ["fx-task-plan-options", "Focused Task"],
  ["fx-task-plan-options", "No Task yet"],
  ["fx-workflow-plan-options", "Delivery workflow (Recommended)"],
  ["fx-workflow-plan-options", "Research workflow"],
  ["fx-workflow-plan-options", "No workflow yet"],
] as const;

for (const [fixtureId, option] of CASES) {
  test(`Plan option can be selected and submitted: ${option}`, async ({ page }) => {
    await page.goto("/?fixture=agent-session");
    const plan = page.getByTestId(fixtureId);
    await expect(plan.getByLabel(option)).toBeVisible();
    await plan.getByLabel(option).check();
    await expect(plan.getByRole("button", { name: "Continue" })).toBeEnabled();
    await plan.getByRole("button", { name: "Continue" }).click();
    await expect(plan).toContainText("Plan summary");
    await expect(plan).toContainText(option);
  });
}

test("Multiple Plan questions advance one at a time and submit atomically", async ({ page }) => {
  await page.goto("/?fixture=agent-session");
  const plan = page.getByTestId("fx-multi-plan-options");

  await expect(plan).toContainText("1 of 2");
  await expect(plan).toContainText("Customize");
  await expect(plan).not.toContainText("Other");
  await expect(plan.getByRole("button", { name: "Chat about" })).toBeEnabled();
  await plan.getByRole("button", { name: "Chat about" }).click();
  await expect(plan.getByTestId("ask-user-question")).toBeVisible();
  await expect(plan.getByTestId("ask-user-question")).toHaveAttribute(
    "data-request-id",
    "evt-multi-clarification:1",
  );
  await plan.getByLabel("Direct (Recommended)").check();
  await plan.getByRole("button", { name: "Next question" }).click();
  await expect(plan).toContainText("2 of 2");
  await plan.getByLabel("Focused (Recommended)").check();
  await plan.getByRole("button", { name: "Continue" }).click();

  await expect(plan).toContainText("Plan summary");
  await expect(plan).toContainText("Direct (Recommended)");
  await expect(plan).toContainText("Focused (Recommended)");
});

test("Plan option cards use radio indicators instead of ordinal numbers", async ({ page }) => {
  await page.goto("/?fixture=agent-session");
  const plan = page.getByTestId("fx-task-plan-options");
  const firstCard = plan.locator(".ask-user-option").first();
  const indicator = firstCard.locator(".ask-user-option-index");

  await expect(indicator).toHaveText("");
  await expect(firstCard.locator('input[type="radio"]')).toHaveCount(1);
  await expect(firstCard.locator(".ask-user-option-arrow")).toHaveCount(0);
  await firstCard.click();
  await expect(firstCard.locator('input[type="radio"]')).toBeChecked();
});

test("Adaptive Channel plan shows selected Profiles without an implicit round cap", async ({ page }) => {
  await page.goto("/?fixture=agent-session");
  const plan = page.getByTestId("fx-adaptive-channel-plan-options");
  await expect(plan).toContainText("Synthesis-adaptive passes");
  await expect(plan).toContainText("Architecture Codex");
  await expect(plan).not.toContainText("0 rounds");
  await expect(plan).not.toContainText("0 round cap");
});
