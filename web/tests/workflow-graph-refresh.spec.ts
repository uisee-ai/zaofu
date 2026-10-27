import { expect, test, type Page } from "@playwright/test";

const req = (id: string) => ({ request_id: id, objective: id, status: "proposed", revision: 1, proposal_digest: id });
const graph = (id: string, seq = 1, label = "Initial graph") => ({
  scope: { request_id: id, source_seq: seq, head_seq: seq, revision: 1 },
  nodes: [{ id: "goal", label, kind: "goal", status: "running", views: ["workflow"], data: {} }],
  edges: [], plans: [], diagnostics: [], versions: [], timeline: [], capabilities: {},
});
async function setup(page: Page) {
  await page.clock.install();
  await page.route("**/api/projects/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/workflow-requests")) return route.fulfill({ json: { items: [req("A"), req("B")] } });
    if (path.endsWith("/graph")) return route.fulfill({ json: graph(path.split("/").at(-2)!) });
    if (path.includes("/workflow-requests/")) return route.fulfill({ json: {
      result: req(path.split("/").at(-1)!), requirement: {}, proposal: {}, artifacts: {}, lifecycle: {}, links: {},
    } });
    return route.fulfill({ json: { ok: true, fields: {} } });
  });
}
async function open(page: Page) {
  await page.goto("/tests/inbox-workflow.html?surface=graph-refresh");
  // The component normally lives in a bounded workspace; this standalone
  // harness must provide the same containing height for its ResizeObserver.
  await page.locator("#root").evaluate(element => { element.style.height = "100vh"; });
  await expect(page.getByTestId("workflow-graph-canvas")).toBeVisible();
  await expect(page.locator('.react-flow__node[data-id="goal"]')).toContainText("Initial graph");
}

test("stable graph uses conditional reconciliation and preserves dragged layout", async ({ page }) => {
  await setup(page);
  let calls = 0;
  await page.route("**/workflow-requests/A/graph*", route => {
    calls++;
    if (calls > 1) {
      expect(route.request().headers()["if-none-match"]).toBe('"stable"');
      return route.fulfill({ status: 304, headers: { ETag: '"stable"' } });
    }
    return route.fulfill({ json: graph("A"), headers: { ETag: '"stable"' } });
  });
  await open(page);
  const node = page.locator('.react-flow__node[data-id="goal"]');
  const box = (await node.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down(); await page.mouse.move(box.x + box.width / 2 + 60, box.y + box.height / 2 + 40, { steps: 8 }); await page.mouse.up();
  const position = await node.getAttribute("style");
  await page.clock.runFor(20_000);
  expect(calls).toBe(1);
  await page.clock.runFor(11_000);
  await expect.poll(() => calls).toBe(2);
  expect(await node.getAttribute("style")).toBe(position);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("event bursts coalesce and a change during a slow read gets one follow-up", async ({ page }) => {
  await setup(page);
  let calls = 0;
  let release!: () => void;
  const delayed = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/workflow-requests/A/graph*", async route => {
    calls++;
    if (calls === 2) {
      await delayed;
      return route.fulfill({ json: graph("A", 2, "Intermediate graph") }).catch(() => {});
    }
    return route.fulfill({ json: graph("A", calls === 1 ? 1 : 20, calls === 1 ? "Initial graph" : "Updated graph") });
  });
  await open(page);
  for (let i = 0; i < 4; i++) await page.getByRole("button", { name: "Graph event", exact: true }).click();
  await page.clock.runFor(300);
  await expect.poll(() => calls).toBe(2);
  for (let i = 0; i < 3; i++) await page.getByRole("button", { name: "Graph event", exact: true }).click();
  await page.clock.runFor(2000);
  expect(calls).toBe(2);
  release();
  await expect(page.locator('.react-flow__node[data-id="goal"]')).toContainText("Intermediate graph");
  await page.clock.runFor(300);
  await expect(page.locator('.react-flow__node[data-id="goal"]')).toContainText("Updated graph");
  expect(calls).toBe(3);
});

test("failed refresh retains data and reconnect reconciles immediately", async ({ page }) => {
  await setup(page);
  let calls = 0;
  await page.route("**/workflow-requests/A/graph*", route => {
    calls++;
    return calls === 2 ? route.fulfill({ status: 503, json: { status: "source_unavailable" } })
      : route.fulfill({ json: graph("A", calls, calls === 1 ? "Initial graph" : "Recovered graph") });
  });
  await open(page);
  await page.getByRole("button", { name: "Graph event", exact: true }).click();
  await page.clock.runFor(300);
  await expect(page.getByRole("alert")).toContainText("503");
  await expect(page.locator('.react-flow__node[data-id="goal"]')).toContainText("Initial graph");
  await page.getByRole("button", { name: "Graph connection", exact: true }).click();
  await page.getByRole("button", { name: "Graph connection", exact: true }).click();
  await page.clock.runFor(300);
  await expect(page.locator('.react-flow__node[data-id="goal"]')).toContainText("Recovered graph");
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("hidden and historical views suspend refresh and old Request responses are discarded", async ({ page }) => {
  await setup(page);
  let calls = 0;
  let release!: () => void;
  const delayed = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/workflow-requests/A/graph*", async route => {
    calls++;
    if (calls === 2) await delayed;
    return route.fulfill({ json: graph("A") }).catch(() => {});
  });
  await open(page);
  await page.evaluate(() => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" }); document.dispatchEvent(new Event("visibilitychange")); });
  await page.getByRole("button", { name: "Graph event", exact: true }).click();
  await page.clock.runFor(31_000); expect(calls).toBe(1);
  await page.evaluate(() => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" }); document.dispatchEvent(new Event("visibilitychange")); });
  await page.clock.runFor(300); await expect.poll(() => calls).toBe(2);
  await page.getByLabel("Select workflow").selectOption("B");
  await expect(page.locator(".sdlc-footer")).toContainText("Event 1");
  release(); await page.clock.runFor(300);
  await expect(page.getByLabel("Select workflow")).toHaveValue("B");
  await expect(page.getByRole("alert")).toHaveCount(0);
  let historyCalls = 0;
  await page.route("**/workflow-requests/B/graph?as_of=1", route => { historyCalls++; return route.fulfill({ json: graph("B") }); });
  await page.getByRole("button", { name: "Graph filters", exact: true }).click();
  await page.getByLabel("回放事件序号").fill("1");
  await page.getByRole("button", { name: "回放", exact: true }).click();
  await expect.poll(() => historyCalls).toBe(1);
  await page.getByRole("button", { name: "Graph event", exact: true }).click();
  await page.clock.runFor(61_000);
  expect(historyCalls).toBe(1);
  await expect(page.locator(".sdlc-footer")).toContainText("Historical snapshot");
});
