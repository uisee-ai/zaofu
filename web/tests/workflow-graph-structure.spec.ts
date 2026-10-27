import { expect, test, type Page } from "@playwright/test";

const request = (id: string) => ({ request_id: id, objective: id, status: "proposed", revision: 1, proposal_digest: id });
const scope = (id: string, seq: number) => ({ request_id: id, revision: 1, run_id: `run-${id}`, proposal_digest: id,
  source_seq: seq, head_seq: seq, historical: false });
const structure = (id: string, seq = 10) => ({ schema_version: "sdlc-workbench-structure.v1", partial: true,
  generation: `generation-${id}-${seq}`, scope: scope(id, seq), diagnostics: [],
  nodes: [{ id: `workflow:stage:${id}`, label: `Structure ${id}`, kind: "workflow", status: "running",
    views: ["workflow"], data: { definition: { type: "stage" } } }], edges: [] });
const full = (id: string, seq = 10) => ({ scope: scope(id, seq), diagnostics: [], capabilities: {}, versions: [], timeline: [],
  nodes: [...structure(id, seq).nodes, { id: `task:${id}`, label: `Full task ${id}`, kind: "task", status: "running",
    views: ["workflow"], data: { nested: { value: "full-search-needle" } } }], edges: [],
  plans: [{ plan_id: `PLAN-${id}`, status: "pending", artifacts: [], refs: {}, action_payload: {} }] });

async function setup(page: Page) {
  await page.route("**/api/projects/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/workflow-requests")) return route.fulfill({ json: { items: [request("A"), request("B")] } });
    if (path.includes("/workflow-requests/")) return route.fulfill({ json: {
      result: request(path.split("/").at(-1)!), requirement: {}, proposal: {}, artifacts: {}, lifecycle: {}, links: {},
    } });
    return route.fulfill({ json: { ok: true, fields: {} } });
  });
}

async function open(page: Page) {
  await page.goto("/tests/inbox-workflow.html?surface=graph-refresh");
  await page.locator("#root").evaluate(element => { element.style.height = "100vh"; });
  await expect(page.getByTestId("workflow-graph-canvas")).toBeVisible();
}

for (const status of [404, 503]) {
  test(`unavailable structure (${status}) falls back once to the original complete graph`, async ({ page }) => {
    await setup(page);
    let structures = 0;
    let details = 0;
    await page.route("**/workflow-requests/A/graph/structure?*", route => {
      structures++;
      return route.fulfill({ status, json: { status: "unavailable" } });
    });
    await page.route("**/workflow-requests/A/graph?*", route => {
      details++;
      expect(route.request().headers()["x-zf-structure-generation"]).toBeUndefined();
      return route.fulfill({ json: full("A") });
    });
    await open(page);
    await expect(page.getByText("Full task A", { exact: true })).toBeVisible();
    expect(structures).toBe(1);
    expect(details).toBe(1);
    await expect(page.getByRole("alert")).toHaveCount(0);
  });
}

test("opening a compiled node loads only its generation-bound detail, not the complete Plans", async ({ page }) => {
  await setup(page);
  let nodes = 0;
  let details = 0;
  await page.route("**/workflow-requests/A/graph/structure?*", route => route.fulfill({ json: structure("A") }));
  await page.route("**/workflow-requests/A/graph?*", route => { details++; return route.fulfill({ json: full("A") }); });
  await page.route("**/workflow-requests/A/graph/node?*", route => {
    nodes++;
    expect(route.request().headers()["x-zf-structure-generation"]).toBe("generation-A-10");
    expect(new URL(route.request().url()).searchParams.get("node_id")).toBe("workflow:stage:A");
    return route.fulfill({ json: { scope: scope("A", 10), node: {
      ...structure("A").nodes[0], data: { definition: { type: "stage", marker: "bound node detail" } },
    } } });
  });
  await open(page);
  await page.locator('.react-flow__node[data-id="workflow:stage:A"]').click();
  const inspector = page.getByRole("complementary", { name: "图节点详情" });
  await expect(inspector.getByText("Structure A", { exact: true })).toBeVisible();
  await inspector.getByText("来源与执行证据", { exact: true }).click();
  await expect(inspector).toContainText("bound node detail");
  expect(nodes).toBe(1);
  expect(details).toBe(0);
  await expect(page.getByRole("button", { name: "Review", exact: true })).toHaveCount(0);
});

test("stale selected-node detail refreshes the structure before retrying", async ({ page }) => {
  await setup(page);
  let structures = 0;
  let nodes = 0;
  let details = 0;
  await page.route("**/workflow-requests/A/graph/structure?*", route => {
    structures++;
    return route.fulfill({ json: structure("A", structures === 1 ? 10 : 11) });
  });
  await page.route("**/workflow-requests/A/graph?*", route => { details++; return route.fulfill({ json: full("A") }); });
  await page.route("**/workflow-requests/A/graph/node?*", route => {
    nodes++;
    if (nodes === 1) return route.fulfill({ status: 409, json: { status: "structure_changed" } });
    expect(route.request().headers()["x-zf-structure-generation"]).toBe("generation-A-11");
    return route.fulfill({ json: { scope: scope("A", 11), node: structure("A", 11).nodes[0] } });
  });
  await open(page);
  await page.locator('.react-flow__node[data-id="workflow:stage:A"]').click();
  await expect(page.getByRole("complementary", { name: "图节点详情" }).getByText("Structure A", { exact: true })).toBeVisible();
  await expect(page.locator(".sdlc-footer")).toContainText("Event 11");
  expect(structures).toBe(2);
  expect(nodes).toBe(2);
  expect(details).toBe(0);
});

test("repeated stale node reads stop after one refresh and permit a manual retry", async ({ page }) => {
  await setup(page);
  let structures = 0;
  let nodes = 0;
  let allow = false;
  await page.route("**/workflow-requests/A/graph/structure?*", route => {
    structures++;
    return route.fulfill({ json: structure("A", 9 + structures) });
  });
  await page.route("**/workflow-requests/A/graph/node?*", route => {
    nodes++;
    if (!allow) return route.fulfill({ status: 409, json: { status: "structure_changed" } });
    return route.fulfill({ json: { scope: scope("A", 9 + structures), node: structure("A", 9 + structures).nodes[0] } });
  });
  await open(page);
  await page.locator('.react-flow__node[data-id="workflow:stage:A"]').click();
  const inspector = page.getByRole("complementary", { name: "图节点详情" });
  await expect(inspector.getByRole("alert")).toHaveText("节点持续更新，请重试。");
  expect(structures).toBe(2);
  expect(nodes).toBe(2);
  allow = true;
  await inspector.getByRole("button", { name: "重试节点详情" }).click();
  await expect(inspector.getByText("Structure A", { exact: true })).toBeVisible();
  expect(nodes).toBe(3);
});

test("structure paints without Plan reads; full search and approval appear only after a bound load", async ({ page }) => {
  await setup(page);
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let reads = 0;
  await page.route("**/workflow-requests/A/graph/structure?*", route => route.fulfill({ json: structure("A") }));
  await page.route("**/workflow-requests/A/graph?*", async route => {
    reads++;
    expect(route.request().headers()["x-zf-structure-generation"]).toBe("generation-A-10");
    await held;
    await route.fulfill({ json: full("A") }).catch(() => {});
  });
  try {
    await open(page);
    await expect(page.getByText("已显示流程结构；运行关系和计划按需读取。", { exact: true })).toBeVisible();
    await expect(page.getByText("Structure A", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Review", exact: true })).toHaveCount(0);
    expect(reads).toBe(0);
    await page.getByRole("button", { name: "Graph filters", exact: true }).click();
    await expect.poll(() => reads).toBe(1);
    await page.getByLabel("搜索图节点").fill("full-search-needle");
    await expect(page.getByText("正在读取完整运行关系…", { exact: true })).toBeVisible();
    await expect(page.getByText("No nodes match these filters.")).toHaveCount(0);
    release();
    await expect(page.getByText("Full task A", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Review", exact: true })).toBeVisible();
    await expect(page.getByTestId("actions")).toHaveText("[]");
  } finally { release(); }
});

test("a changed generation reloads structure and retries once before showing full data", async ({ page }) => {
  await setup(page);
  let structures = 0;
  let details = 0;
  await page.route("**/workflow-requests/A/graph/structure?*", route => {
    structures++;
    return route.fulfill({ json: structure("A", structures === 1 ? 10 : 11) });
  });
  await page.route("**/workflow-requests/A/graph?*", route => {
    details++;
    const generation = route.request().headers()["x-zf-structure-generation"];
    if (details === 1) {
      expect(generation).toBe("generation-A-10");
      return route.fulfill({ status: 409, json: { status: "structure_changed" } });
    }
    expect(generation).toBe("generation-A-11");
    return route.fulfill({ json: full("A", 11) });
  });
  await open(page);
  await page.getByRole("button", { name: "加载完整运行图和计划" }).click();
  await expect(page.getByRole("button", { name: "Review", exact: true })).toBeVisible();
  await expect(page.locator(".sdlc-footer")).toContainText("Event 11");
  expect(structures).toBe(2);
  expect(details).toBe(2);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("repeated generation changes stop retrying and keep the partial view until an explicit retry", async ({ page }) => {
  await setup(page);
  let structures = 0;
  let details = 0;
  let allow = false;
  await page.route("**/workflow-requests/A/graph/structure?*", route => {
    structures++;
    return route.fulfill({ json: structure("A", 9 + structures) });
  });
  await page.route("**/workflow-requests/A/graph?*", route => {
    details++;
    if (!allow) return route.fulfill({ status: 409, json: { status: "structure_changed" } });
    expect(route.request().headers()["x-zf-structure-generation"]).toBe(`generation-A-${9 + structures}`);
    return route.fulfill({ json: full("A", 9 + structures) });
  });
  await open(page);
  await page.getByRole("button", { name: "加载完整运行图和计划" }).click();
  await expect(page.getByRole("alert")).toContainText("HTTP 409");
  expect(structures).toBe(2);
  expect(details).toBe(2);
  await expect(page.getByText("Structure A", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Review", exact: true })).toHaveCount(0);
  allow = true;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByRole("button", { name: "Review", exact: true })).toBeVisible();
  expect(structures).toBe(3);
  expect(details).toBe(3);
  await expect(page.getByTestId("actions")).toHaveText("[]");
});

test("late full detail cannot replace the newly selected request", async ({ page }) => {
  await setup(page);
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let started = false;
  await page.route("**/workflow-requests/*/graph/structure?*", route => {
    const id = new URL(route.request().url()).pathname.split("/").at(-3)!;
    return route.fulfill({ json: structure(id) });
  });
  await page.route("**/workflow-requests/A/graph?*", async route => {
    started = true; await held;
    await route.fulfill({ json: full("A") }).catch(() => {});
  });
  try {
    await open(page);
    await page.getByRole("button", { name: "加载完整运行图和计划" }).click();
    await expect.poll(() => started).toBe(true);
    await page.getByLabel("Select workflow").selectOption("B");
    await expect(page.getByText("Structure B", { exact: true })).toBeVisible();
    release();
    await expect(page.getByText("Full task A", { exact: true })).toHaveCount(0);
    await expect(page.getByLabel("Select workflow")).toHaveValue("B");
  } finally { release(); }
});
