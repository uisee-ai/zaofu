import { expect, test } from "@playwright/test";
import { installWorkspacePages } from "./workspace-pages.fixture";

test.use({ locale: "en-US" });
test.describe.configure({ timeout: 90_000 });

test("Inbox filters remain visible and selectable on desktop and narrow screens", async ({ page }) => {
  const { writes, errors } = await installWorkspacePages(page);
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/?project=ui-review&page=inbox");
    const filters = page.getByLabel("Inbox display view");
    const names = ["All", "Needs action", "Resolved"];
    for (const name of names) {
      const button = filters.getByRole("button", { name, exact: true });
      await expect(button).toBeVisible();
      const fits = await button.evaluate(el => {
        const bounds = el.getBoundingClientRect(), parent = el.parentElement!.getBoundingClientRect();
        return bounds.left >= parent.left && bounds.right <= parent.right && bounds.top >= parent.top && bounds.bottom <= parent.bottom;
      });
      expect(fits, `${name} at ${width}px`).toBe(true);
      await button.click();
      await expect(button).toHaveAttribute("aria-pressed", "true");
    }
    const row = await filters.locator("button, select").evaluateAll(elements => elements.map(el => el.getBoundingClientRect().top));
    expect(Math.max(...row) - Math.min(...row)).toBeLessThan(8);
    const more = page.getByLabel("More inbox views");
    for (const value of ["runtime_attention", "notification", "automation"]) {
      await more.selectOption(value);
      await expect(more).toHaveValue(value);
    }
    await page.getByRole("checkbox", { name: "Unread", exact: true }).check();
    await expect(page.getByRole("checkbox", { name: "Unread", exact: true })).toBeChecked();
    await expect(page.getByRole("button", { name: "Mark all read" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  expect(writes).toEqual([]); expect(errors).toEqual([]);
});

test("Workflow keeps provider, status, readable mobile nodes and an explicit full-graph fit", async ({ page }) => {
  const { writes, errors } = await installWorkspacePages(page);
  const views = ["lifecycle", "modules", "workflow", "trace", "delivery", "timeline"];
  const nodes = Array.from({ length: 40 }, (_, index) => ({
    id: `node-${index}`, kind: "operation", label: `Review task ${index}`,
    status: index === 20 ? "running" : "pending", views,
    data: { agent_binding: { backend: "claude-code", source: "provider_usage" } },
  }));
  await page.route("**/workflow-requests/REQ-UI/graph*", route => route.fulfill({ json: {
    scope: { request_id: "REQ-UI", revision: 1, run_id: "", head_seq: 1, source_seq: 1, proposal_digest: "fixture", historical: false },
    nodes, edges: [], diagnostics: [], capabilities: {}, plans: [{ plan_id: "fixture-plan", status: "pending", markdown: "# Review fixture", artifacts: [], actions: {}, action_payload: {} }], versions: [], timeline: [], analysis: { intervals: [], critical_path: { status: "unknown", node_ids: [], duration_ms: null, method: "" } },
  } }));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/?project=ui-review&page=workflows&graph=workflow");
  const canvas = page.getByTestId("workflow-graph-canvas");
  await expect(canvas).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel("Select workflow")).toHaveCount(0);
  await expect(page.locator(".workflow-proposal-toolbar")).not.toContainText("Align the workspace");
  const review = page.getByRole("button", { name: "Review", exact: true });
  await expect(review).toBeVisible();
  await expect(page.getByText("Plan is ready. Review it before implementation continues.")).toHaveCount(0);
  const card = canvas.locator(".sdlc-card").first();
  await expect(card).toContainText("claude-code");
  await expect(card).toContainText("pending");
  await expect(card).toHaveCSS("border-top-width", "1px");
  await expect(card.locator("strong")).toHaveCSS("font-size", "13.5px");
  await expect(card.locator("strong")).toHaveCSS("font-family", /Geist Variable/);
  await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; });
  await expect(card).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  const reviewBox = (await review.boundingBox())!, liveBox = (await page.locator(".sdlc-live").boundingBox())!;
  expect(reviewBox.x).toBeGreaterThan(liveBox.x + liveBox.width);
  expect(Math.abs((reviewBox.y + reviewBox.height / 2) - (liveBox.y + liveBox.height / 2))).toBeLessThan(2);
  await expect.poll(async () => (await canvas.locator(".react-flow__node").first().boundingBox())?.width || 0).toBeGreaterThan(160);
  await page.getByRole("button", { name: /fit view/i }).click();
  await expect.poll(() => canvas.locator(".react-flow__node").count()).toBe(40);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await review.click();
  await expect(page.getByRole("region", { name: "Workflow PRD Plan Preview" })).toBeVisible();
  await page.getByRole("button", { name: "关闭预览" }).click();
  expect(writes).toEqual([]); expect(errors).toEqual([]);
});
