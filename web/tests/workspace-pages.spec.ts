import { expect, test, type Page } from "@playwright/test";
import { installWorkspacePages } from "./workspace-pages.fixture";

test.use({ viewport: { width: 1440, height: 1000 }, locale: "en-US" });
test.describe.configure({ timeout: 90_000 });
const open = (page: Page, route: string) => page.goto(`/?project=ui-review&page=${route}`);
async function noOverflow(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(await page.evaluate(() => innerWidth)); }

test("Overview prioritizes actionable work and preserves pulse-first loading", async ({ page }, info) => {
  const { writes, errors } = await installWorkspacePages(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route(/\/snapshot(?:\/(?:light|board))?$/, async (route) => { await gate; return route.fallback(); });
  try {
    await open(page, "project");
    await expect(page.getByTestId("overview-pulse-band")).toContainText("7s");
    await expect(page.locator(".overview-metrics .workspace-stat strong")).toHaveText(["—", "—", "—", "—", "—"]);
    await expect(page.getByTestId("overview-dashboard")).not.toContainText("Runtime offline");
  } finally { release(); }
  await expect(page.getByRole("heading", { name: "ZaoFu Studio", exact: true })).toBeVisible();
  await expect(page.locator(".overview-heading h2")).toHaveCSS("font-size", "24px");
  await expect(page.locator(".overview-metrics .workspace-stat strong")).toHaveText(["5", "17%", "1", "—", "0"]);
  await expect(page.locator(".overview-metrics .workspace-stat > span")).toHaveText(["Work Queue", "Delivery", "Features", "Cost", "Attention"]);
  const main = await page.locator(".overview-main").boundingBox(), aside = await page.locator(".overview-aside").boundingBox();
  expect(main!.width).toBeGreaterThan(aside!.width); expect(main!.x + main!.width).toBeLessThan(aside!.x);
  await expect(page.locator(".overview-columns > :first-child")).toHaveClass(/overview-flow/);
  const cards = await page.locator(".overview-metrics .workspace-stat").evaluateAll((nodes) => nodes.map((node) => {
    const box = node.getBoundingClientRect(), style = getComputedStyle(node);
    return { x: box.x, right: box.right, bottom: box.bottom, border: style.borderLeftWidth, radius: style.borderRadius };
  }));
  for (let index = 0; index < cards.length; index++) {
    expect(cards[index].border).toBe("1px"); expect(cards[index].radius).toBe("10px");
    if (index) expect(cards[index].x - cards[index - 1].right).toBeGreaterThanOrEqual(15);
  }
  const execution = await page.locator(".overview-flow").boundingBox();
  expect(Math.abs(execution!.x - cards[0].x)).toBeLessThan(1);
  expect(Math.abs(execution!.x + execution!.width - cards[0].right)).toBeLessThan(1);
  expect(execution!.y - cards[0].bottom).toBeGreaterThanOrEqual(15);
  expect(execution!.y - cards[0].bottom).toBeLessThanOrEqual(17);
  await expect(page.locator(".overview-activity")).toContainText("task · started");
  await expect(page.locator(".workspace-stat strong").first()).toHaveCSS("font-size", "24px");
  await page.getByRole("button", { name: "Read more", exact: true }).click();
  await expect(page.getByRole("button", { name: "Show less", exact: true })).toHaveAttribute("aria-expanded", "true");
  await page.screenshot({ path: info.outputPath("overview-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 }); await noOverflow(page);
  await expect(page.locator(".overview-metrics .workspace-stat").nth(2)).toHaveCSS("border-left-width", "1px");
  await page.getByRole("button", { name: "Show less", exact: true }).click();
  await page.screenshot({ path: info.outputPath("overview-mobile.png") });
  await page.getByRole("button", { name: "Open Tasks", exact: true }).click();
  await expect(page.locator(".task-card")).toHaveCount(6);
  expect(writes).toEqual([]); expect(errors).toEqual([]);
});

test("Agents filters and opens a keyboard-safe worker drawer", async ({ page }, info) => {
  const { writes, errors } = await installWorkspacePages(page);
  await open(page, "agents");
  await expect(page.locator(".fleet-agent-open")).toHaveCount(2);
  await page.getByLabel("Search agents", { exact: true }).fill("reviewer");
  await expect(page.locator(".fleet-agent-open")).toHaveCount(1);
  await page.getByLabel("Search agents", { exact: true }).fill("");
  await page.getByRole("group", { name: "Agent filter" }).getByRole("button", { name: "Needs attention", exact: true }).click();
  await expect(page.locator(".fleet-agent-open")).toHaveCount(1);
  await page.locator(".fleet-agent-open").click();
  const drawer = page.getByRole("dialog", { name: "Worker details" });
  await expect(drawer).toContainText("reviewer-1");
  await page.keyboard.press("Escape"); await expect(drawer).toHaveCount(0);
  await expect(page.locator(".fleet-agent-open")).toBeFocused();
  await page.screenshot({ path: info.outputPath("agents-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 }); await noOverflow(page);
  await page.locator(".fleet-agent-open").click(); await expect(drawer).toBeVisible();
  await page.screenshot({ path: info.outputPath("agents-mobile-drawer.png") });
  expect(writes).toEqual([]); expect(errors).toEqual([]);
});

test("Automations separates reports from history and keeps explicit run actions", async ({ page }, info) => {
  const { writes, errors } = await installWorkspacePages(page);
  await open(page, "automations");
  await expect(page.locator(".automation-directory > button")).toHaveCount(2);
  await page.locator(".automation-directory > button").filter({ hasText: "Weekly review" }).click();
  await expect(page.locator(".automation-control-title")).toContainText("Weekly review");
  await page.getByRole("button", { name: "Run history", exact: true }).click();
  await expect(page.locator("#automation-runs")).toContainText("completed");
  await page.getByRole("button", { name: "Proposals", exact: true }).click();
  await expect(page.locator("#automation-proposals")).toContainText("Review blocked acceptance");
  await page.getByRole("button", { name: "Report", exact: true }).click();
  await expect(page.locator(".workspace-disclosure")).not.toHaveAttribute("open", "");
  expect(writes).toEqual([]);
  await page.getByRole("button", { name: "Run now", exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(JSON.stringify(writes[0])).toContain("weekly-review");
  await page.screenshot({ path: info.outputPath("automations-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 }); await noOverflow(page);
  await page.screenshot({ path: info.outputPath("automations-mobile.png") });
  expect(errors).toEqual([]);
});

test("locked Automation controls cannot run", async ({ page }) => {
  const { writes, errors } = await installWorkspacePages(page, { locked: true });
  await open(page, "automations");
  await expect(page.getByRole("button", { name: "Run now", exact: true })).toBeDisabled();
  expect(writes).toEqual([]); expect(errors).toEqual([]);
});

test("Delivery, Workflows and monitoring retain their main views in both themes and widths", async ({ page }, info) => {
  const { writes, errors } = await installWorkspacePages(page);
  for (const [route, selector] of [["delivery", ".delivery-overview"], ["workflows", ".workflow-proposal-detail-head"], ["traces", ".trace-index-panel"], ["behavior-loop", ".loop-v2-page"]]) {
    await page.setViewportSize({ width: 1440, height: 1000 }); await open(page, route);
    await expect(page.locator(selector)).toBeVisible();
    if (route === "workflows") {
      await expect(page.locator('.workflow-request-row')).toContainText("Align the workspace");
      await expect(page.getByTestId("workflow-readiness")).toBeVisible();
      await expect(page.getByTestId("workflow-approve-run")).toBeEnabled();
      await expect(page.locator(".workflow-graph-disclosure")).not.toHaveAttribute("open", "");
      await page.getByText("Workflow graph", { exact: true }).click();
      await expect(page.locator(".react-flow")).toBeVisible();
      await page.getByText("Workflow graph", { exact: true }).click();
    }
    if (route === "traces") await expect(page.locator(".trace-index-panel")).toContainText("trace-ui");
    await page.screenshot({ path: info.outputPath(`${route}-desktop.png`) });
    await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; });
    await page.setViewportSize({ width: 390, height: 844 }); await noOverflow(page);
    await page.screenshot({ path: info.outputPath(`${route}-mobile-dark.png`) });
  }
  expect(writes).toEqual([]); expect(errors).toEqual([]);
});

test("Channels keeps conversation, composer and flow navigation usable", async ({ page }, info) => {
  const { writes, errors } = await installWorkspacePages(page);
  await page.goto("/?project=ui-review&page=channels&channel=ui-channel");
  await expect(page.locator(".channel-timeline")).toContainText("Keep the existing navigation");
  await expect(page.getByTestId("channel-flow-nav")).toBeVisible();
  await page.locator(".channel-composer-input").fill("Review the updated layout.");
  await page.screenshot({ path: info.outputPath("channels-desktop.png") });
  for (const width of [390, 360]) {
    await page.setViewportSize({ width, height: 844 }); await noOverflow(page);
    await expect(page.locator(".channel-composer-input")).toBeInViewport();
    await expect(page.getByTestId("channel-flow-nav")).toBeInViewport();
    await expect(page.locator(".channel-composer-input")).toHaveText("Review the updated layout.");
  }
  await page.screenshot({ path: info.outputPath("channels-mobile.png") });
  expect(writes).toEqual([]); expect(errors).toEqual([]);
});

test("locked worker and workflow controls remain unavailable", async ({ page }) => {
  const { writes, errors } = await installWorkspacePages(page, { locked: true });
  await open(page, "agents");
  await page.locator(".fleet-agent-open").filter({ hasText: "developer-1" }).click();
  const drawer = page.getByRole("dialog", { name: "Worker details" });
  await expect(drawer.getByRole("button", { name: "Reply", exact: true })).toBeDisabled();
  await expect(drawer.getByRole("button", { name: "Drain", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await open(page, "workflows");
  await expect(page.getByTestId("workflow-readiness")).toBeVisible();
  await expect(page.getByTestId("workflow-approve-run")).toBeDisabled();
  expect(writes).toEqual([]); expect(errors).toEqual([]);
});

test("Overview and Agents show truthful empty states", async ({ page }) => {
  const { writes, errors } = await installWorkspacePages(page, { empty: true });
  await open(page, "project");
  await expect(page.locator(".overview-metrics .workspace-stat strong")).toHaveText(["0", "0%", "0", "—", "0"]);
  await expect(page.locator(".overview-deliveries")).toContainText("No features yet");
  await expect(page.locator(".overview-activity")).toContainText("No recent activity");
  await expect(page.locator(".project-focus-section")).not.toContainText("Blocked work");
  await open(page, "agents");
  await expect(page.getByText("No agents are running in this project.", { exact: true })).toBeVisible();
  await expect(page.locator(".fleet-agent-open")).toHaveCount(0);
  expect(writes).toEqual([]); expect(errors).toEqual([]);
});

for (const failure of ["error", "timeout"]) {
  test(`Agents distinguishes ${failure} from an empty fleet and supports retry`, async ({ page }) => {
    const { writes, errors } = await installWorkspacePages(page);
    let failed = false;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    if (failure === "timeout") await page.clock.install();
    await page.route(/\/agent-monitoring$/, async (route) => {
      if (failed) return route.fallback();
      failed = true;
      if (failure === "error") return route.fulfill({ status: 503, json: { error: "Temporarily unavailable" } });
      await gate;
      await route.abort().catch(() => undefined);
    });
    try {
      await open(page, "agents");
      if (failure === "timeout") {
        await expect(page.getByText("Loading agents…", { exact: true }).first()).toBeVisible();
        await expect.poll(() => failed).toBe(true);
        await page.clock.fastForward(20_001);
      }
      await expect(page.getByText("Could not load agents. Try again.", { exact: true })).toBeVisible();
      await expect(page.getByText("No agents are running in this project.", { exact: true })).toHaveCount(0);
      await expect(page.locator(".runtime-summary-card").first().locator("strong")).toHaveText("—");
      release();
      await page.getByRole("button", { name: "Retry agents", exact: true }).click();
      await expect(page.locator(".fleet-agent-open")).toHaveCount(2);
      expect(writes).toEqual([]); expect(errors).toEqual([]);
    } finally { release(); }
  });
}

test("Overview contains a populated execution flow within its card", async ({ page }, info) => {
  const { writes, errors } = await installWorkspacePages(page);
  await page.route("**/overview-pulse", (route) => route.fulfill({ json: {
    schema_version: "overview-pulse.v1",
    run_pulse: { last_event_age_seconds: 7, events_per_bucket: [1, 2], bucket_seconds: 60, respawn_failed_streak: 0, loop: { status: "running", age_seconds: 4 }, sessions: { active: 2, stale: 0 } },
    task_flow: { columns: { todo: 36, in_progress: 1, verify: 0, done: 0, blocked: 1 }, oldest_age_seconds: { todo: 172800, in_progress: 172800 }, transitions_per_hour: { todo_to_in_progress: 0, in_progress_to_verify: 0 }, wip: { used: 1, capacity: 8 }, window_hours: 24, done_gate: "approved", rework_backedge_per_hour: 0,
      blocked_side_pocket: [{ task_id: "UI-104", reason: "Waiting-for-workflow-approval-and-owner-acceptance-evidence-with-a-long-reference", age_seconds: 212400 }] },
    why_not: { summary: "waiting_for_owner", notifications: [{ reason: "Review the pending workflow approval before continuing execution." }] },
  } }));
  await open(page, "project");
  const flow = page.locator(".overview-flow");
  await expect(flow).toContainText("WIP 1/8");
  await expect(flow).toContainText("Rework");
  for (const width of [1440, 1200, 900, 390, 360]) {
    await page.setViewportSize({ width, height: 1000 });
    const geometry = await flow.evaluate((card) => {
      const bounds = card.getBoundingClientRect();
      const queue = document.querySelector(".overview-metrics .workspace-stat")!.getBoundingClientRect();
      const rail = card.querySelector<HTMLElement>(".task-flow-chain-rail")!;
      const content = [...card.querySelectorAll<HTMLElement>(".overview-card-heading, .task-flow-band, .task-flow-chain, .task-flow-chain-rail, .task-flow-node, .task-flow-edge, .task-flow-rework, .task-flow-meta, .task-flow-pocket, .task-flow-whynot")];
      const nodes = [...card.querySelectorAll(".task-flow-node")].map((node) => node.getBoundingClientRect());
      return { aligned: Math.abs(bounds.left - queue.left), widthDifference: Math.abs(bounds.width - queue.width), overflow: card.scrollWidth - card.clientWidth,
        contained: content.every((node) => { const box = node.getBoundingClientRect(); return box.left >= bounds.left && box.right <= bounds.right; }),
        vertical: nodes.every((node, index) => !index || node.top > nodes[index - 1].bottom),
        railOverflow: rail.scrollWidth - rail.clientWidth };
    });
    expect(geometry.aligned).toBeLessThan(1);
    if (width > 720) expect(geometry.widthDifference).toBeLessThan(1);
    expect(geometry.overflow).toBeLessThanOrEqual(1);
    expect(geometry.contained).toBe(true);
    expect(geometry.vertical).toBe(true);
    expect(geometry.railOverflow).toBeLessThanOrEqual(1);
    await noOverflow(page);
    if (width === 1440 || width === 390) await page.screenshot({ path: info.outputPath(`overview-flow-${width}.png`) });
  }
  await flow.getByRole("button", { name: /blocked \(side pocket\)/ }).click();
  await expect(page).toHaveURL(/page=task/);
  await expect(page.getByRole("textbox", { name: "Title", exact: true })).toHaveValue("Resolve missing acceptance evidence");
  expect(writes).toEqual([]); expect(errors).toEqual([]);
});
