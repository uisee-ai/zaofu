import { expect, test } from "@playwright/test";

const value = (seq: number, state = "ready") => ({
  project_id: "fixture", status: "ready", source_seq: seq,
  metrics_snapshot: { tokens: seq * 100, cost: 0.123456789 },
  read_projection: { state },
});

test("first projection waits without zero metrics; stale data survives until the worker catches up", async ({ page }) => {
  await page.clock.install();
  let calls = 0;
  await page.route("**/api/projects/fixture/agent-monitoring", route => {
    calls++;
    if (calls === 1) return route.fulfill({ status: 202, json: { read_projection: { state: "building" } } });
    if (calls === 3) return route.fulfill({ json: value(1, "refreshing") });
    return route.fulfill({ json: value(calls >= 4 ? 2 : 1) });
  });
  await page.goto("/tests/agent-monitoring.html");
  await expect(page.locator("output")).toHaveText("No projection");
  await expect.poll(() => calls).toBe(1);
  await page.clock.runFor(1100);
  await expect(page.locator("output")).toContainText('"tokens":100');
  await page.getByRole("button", { name: "Event", exact: true }).click();
  await page.clock.runFor(300);
  await expect.poll(() => calls).toBe(3);
  await expect(page.locator("output")).toContainText('"tokens":100');
  await expect(page.getByRole("status").first()).toHaveText("Refreshing");
  await page.clock.runFor(1400);
  await expect(page.locator("output")).toContainText('"tokens":200');
  await expect(page.locator("output")).toContainText('"cost":0.123456789');
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("worker failure preserves the complete last result and hidden tabs pause refresh", async ({ page }) => {
  await page.clock.install();
  let calls = 0;
  await page.route("**/api/projects/fixture/agent-monitoring", route => {
    calls++;
    return calls === 2 ? route.fulfill({ status: 503, json: { read_projection: { state: "error" } } })
      : route.fulfill({ json: value(calls) });
  });
  await page.goto("/tests/agent-monitoring.html");
  await expect(page.locator("output")).toContainText('"tokens":100');
  await page.getByRole("button", { name: "Event", exact: true }).click();
  await page.clock.runFor(300);
  await expect(page.getByRole("alert")).toContainText("503");
  await expect(page.locator("output")).toContainText('"tokens":100');
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.clock.runFor(30000);
  expect(calls).toBe(2);
});
