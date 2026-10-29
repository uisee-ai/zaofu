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

test("timeline shows observed stage operations and preserves unknown times", async ({ page }) => {
  await setup(page);
  await page.route("**/workflow-requests/A/graph/structure?*", route => route.fulfill({ status: 404 }));
  const graph = { ...full("A"), nodes: [
    { id: "workflow:stage:impl", kind: "workflow", label: "Implementation", status: "running", views: ["workflow"], data: { stage_id: "impl" } },
    { id: "workflow:stage:verify", kind: "workflow", label: "Verification", status: "pending", views: ["workflow"], data: { stage_id: "verify" } },
    ...["a", "b", "c"].map(id => ({ id: `operation:${id}`, kind: "operation", label: `Agent ${id}`, status: "running", views: ["timeline"], data: { parent_stage_id: "impl" } })),
  ], analysis: { intervals: [
    { operation_id: "a", task_id: "TASK-1", role: "dev-1", status: "settled", start_ms: 1000000, end_ms: 1060000, duration_ms: 60000 },
    { operation_id: "b", task_id: "TASK-1", role: "dev-2", status: "running", start_ms: 1030000, end_ms: null, duration_ms: null },
    { operation_id: "c", task_id: "TASK-2", role: "verify", status: "pending", start_ms: null, end_ms: null, duration_ms: null },
  ] } };
  await page.route("**/workflow-requests/A/graph?*", route => route.fulfill({ json: graph }));
  await open(page);
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  const timeline = page.getByTestId("workflow-execution-timeline");
  await expect(timeline).toContainText("Implementation");
  await expect(timeline).toContainText("Verification");
  await expect(timeline.getByTestId("workflow-execution-row")).toHaveCount(3);
  await expect(timeline).toContainText("结束时间未观测");
  await expect(timeline).toContainText("开始时间未观测");
  await expect(timeline).toContainText("尚无已观测操作");
  await expect(timeline.getByRole("button", { name: "压缩空档" })).toHaveAttribute("aria-pressed", "true");
  await timeline.getByRole("button", { name: "真实时间" }).click();
  await expect(timeline).toContainText("横轴按真实时间比例");
  await timeline.getByTestId("workflow-execution-row").first().getByRole("button", { name: "详情", exact: true }).click();
  await expect(page.getByRole("complementary", { name: "图节点详情" })).toContainText("Agent a");
  await expect(page.locator(".sdlc-footer")).toContainText("3 operations");
  await expect(page.getByTestId("workflow-event-timeline")).toHaveCount(0);
});

test("large real-sized graphs remain readable and disclose omitted relationships", async ({ page }, testInfo) => {
  await setup(page);
  await page.route("**/workflow-requests/A/graph/structure?*", route => route.fulfill({ status: 404 }));
  const large = { ...full("A"), nodes: [{ id: "root", kind: "requirement", label: "Readable goal", status: "confirmed", views: ["modules"], data: {} },
    ...Array.from({ length: 120 }, (_, index) => ({ id: `task:${index}`, kind: "task", label: `Task ${index}`,
      status: index === 0 ? "failed" : "pending", views: ["modules"], data: {} }))] };
  await page.route("**/workflow-requests/A/graph?*", route => route.fulfill({ json: large }));
  await open(page);
  await page.getByRole("button", { name: "Task", exact: true }).click();
  await expect(page.getByText(/仅显示 40 个重点节点/)).toBeVisible();
  await expect(page.getByText(/81 个节点已折叠/)).toBeVisible();
  const goal = page.locator('.react-flow__node[data-id="root"]');
  await expect(goal).toBeVisible();
  const box = await goal.boundingBox();
  expect(box?.width).toBeGreaterThan(120);
  await page.screenshot({ path: testInfo.outputPath("large-goal-graph.png") });
  await page.getByRole("button", { name: "显示全部", exact: true }).click();
  await expect(page.getByRole("button", { name: "返回重点节点" })).toBeVisible();
});

test("task context selects an exact bound request and explicit request takes precedence", async ({ page }) => {
  await setup(page);
  await page.route("**/api/projects/*/workflow-requests", route => route.fulfill({ json: { items: [
    { ...request("A"), task_id: "other" }, { ...request("B"), task_id: "TASK-1", status: "running" },
  ] } }));
  await page.route("**/workflow-requests/B/graph/structure?*", route => route.fulfill({ json: structure("B") }));
  await page.goto("/tests/inbox-workflow.html?surface=graph-refresh&task=TASK-1");
  await expect(page.getByLabel("Select workflow")).toHaveValue("B");
  await page.goto("/tests/inbox-workflow.html?surface=graph-refresh&task=TASK-1&request=A");
  await expect(page.getByLabel("Select workflow")).toHaveValue("A");
});

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
