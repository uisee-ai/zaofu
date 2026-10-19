import { expect, test } from "@playwright/test";

test("durable pending controls expose truthful fallback and safe restore", async ({ page }) => {
  await page.goto("/?fixture=agent-session");
  const composer = page.getByTestId("fx-webchat-pending");

  await expect(composer.getByText("Steer queued", { exact: true })).toBeVisible();
  await expect(composer.getByText("Interrupting and resuming", { exact: true })).toBeVisible();
  await composer.getByRole("button", { name: "Edit queued message" }).click();
  await expect(composer.getByLabel("Restored queued message")).toHaveValue("Keep the public API stable");
  await expect(composer.getByText("Removed from queue", { exact: true })).toBeVisible();
  await expect(composer.getByRole("button", { name: "Remove queued message" })).toHaveCount(0);
});

test("Ask User keeps one active surface and preserves its multi-question draft", async ({ page }) => {
  await page.goto("/?fixture=agent-session");
  const composer = page.getByTestId("fx-webchat-interaction");
  const thread = page.getByTestId("fx-webchat-interaction-thread");

  await expect(composer.getByTestId("webchat-interaction-composer")).toBeVisible();
  await expect(thread.getByRole("button", { name: "Answering below" })).toBeVisible();
  await composer.getByRole("radio", { name: /Other/ }).click();
  await composer.getByLabel("Answer for How should this change be delivered?").fill("Keep the public contract");

  await composer.getByRole("button", { name: "Move question to conversation" }).click();
  await expect(composer.getByTestId("webchat-interaction-composer")).toHaveCount(0);
  const threadPanel = thread.getByTestId("webchat-interaction-thread");
  await expect(threadPanel.getByRole("radio", { name: /Other/ })).toHaveAttribute("aria-checked", "true");
  await expect(threadPanel.getByLabel("Answer for How should this change be delivered?")).toHaveValue("Keep the public contract");

  await threadPanel.getByRole("button", { name: "Move question to composer" }).click();
  await composer.getByRole("button", { name: /Next/ }).click();
  await composer.getByRole("checkbox", { name: "Focused tests" }).click();
  await composer.getByRole("checkbox", { name: "Browser verification" }).click();
  await composer.getByRole("button", { name: /Submit/ }).click();

  const response = page.getByTestId("webchat-interaction-response");
  await expect(response).toContainText("Keep the public contract");
  await expect(response).toContainText('"option_ids":["tests","browser"]');
  await expect(composer.getByTestId("webchat-interaction-composer")).toHaveCount(0);
  await expect(thread.getByTestId("webchat-interaction-thread")).toHaveCount(0);
});

test("completed activity folds below the final answer without leaking channel reasoning", async ({ page }) => {
  await page.goto("/?fixture=agent-session");
  const kanban = page.getByTestId("fx-kanban-compact");
  const finalAnswer = kanban.getByText(/Done .* the build passed but 2 tests fail/);
  const failedActivity = kanban.getByText("Activity needs attention", { exact: true }).first();

  await expect(finalAnswer).toBeVisible();
  await expect(failedActivity).toBeVisible();
  const answerBox = await finalAnswer.boundingBox();
  const activityBox = await failedActivity.boundingBox();
  expect(answerBox?.y).toBeLessThan(activityBox?.y ?? 0);
  await expect(kanban.getByText("2 failed, 18 passed", { exact: true })).toBeVisible();

  const channel = page.getByTestId("fx-channel-compact");
  await expect(channel.getByText(/Done .* the build passed but 2 tests fail/)).toBeVisible();
  await expect(channel.getByText("Plan: read the file, run the suite, summarize failures.")).toHaveCount(0);
});

test("interaction controls remain inside a mobile viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 });
  await page.goto("/?fixture=agent-session");
  const panel = page.getByTestId("fx-webchat-interaction").getByTestId("webchat-interaction-composer");
  const bounds = await panel.boundingBox();
  expect(bounds?.x).toBeGreaterThanOrEqual(0);
  expect((bounds?.x ?? 0) + (bounds?.width ?? 0)).toBeLessThanOrEqual(390);
  await expect(panel.getByRole("radio")).toHaveCount(3);
});
