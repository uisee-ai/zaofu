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

test("standard stages use readable names and waiting discovery remains available", async ({ page }, testInfo) => {
  test.setTimeout(60_000);
  await setup(page);
  await page.route("**/workflow-requests/A/graph/structure?*", route => route.fulfill({ status: 404 }));
  const stage = (id: string, status: string) => ({ id: `workflow:stage:${id}`, kind: "workflow", label: id, status, views: ["workflow"], data: { stage_id: id, definition: { type: "fanout_stage" } } });
  const graph = { ...full("A", 1), nodes: [stage("prd-scan", "completed"), stage("prd-lanes-final", "pending"), stage("prd-post-verify-discovery", "pending"),
    { id: "workflow:terminal:done", kind: "workflow", label: "terminal done", status: "pending", views: ["workflow"], data: { definition: { type: "terminal_gate" } } }], edges: [] };
  await page.route("**/workflow-requests/A/graph?*", route => route.fulfill({ json: graph }));
  await open(page);
  await expect(page.locator('.react-flow__node[data-id="workflow:stage:prd-scan"]')).toContainText("需求扫描");
  await expect(page.locator('.react-flow__node[data-id="workflow:stage:prd-lanes-final"]')).toContainText("最终验收");
  const gate = page.locator('.react-flow__node[data-id="workflow:terminal:done"]');
  await expect(gate).toContainText("结束门禁");
  await expect(gate).toContainText("等待");
  expect((await gate.boundingBox())!.width).toBeLessThan((await page.locator('.react-flow__node[data-id="workflow:stage:prd-scan"]').boundingBox())!.width);
  const discovery = page.locator('.react-flow__node[data-id="workflow:stage:prd-post-verify-discovery"]');
  await expect(discovery).toHaveCount(0);
  await expect(page.getByText("其他阶段：", { exact: false })).toHaveCount(0);
  await page.getByRole("button", { name: "Graph filters" }).click();
  await page.getByRole("button", { name: "展开角色、汇聚与事件结构" }).click();
  await expect(discovery).toBeVisible();
  await discovery.click();
  await expect(page.getByRole("complementary", { name: "图节点详情" })).toContainText("workflow:stage:prd-post-verify-discovery");
  await page.getByRole("button", { name: "Close node details" }).click();
  await page.getByRole("button", { name: "返回阶段与执行概览" }).click();
  graph.nodes[2].status = "failed"; graph.scope.head_seq = 2; graph.scope.source_seq = 2;
  await page.getByRole("button", { name: "Graph event", exact: true }).click();
  await expect(discovery).toContainText("失败");
  await expect(page.getByText("其他阶段：", { exact: false })).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("readable-workflow-mobile.png") });
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto("/tests/inbox-workflow.html?surface=graph-refresh&request=A&node=workflow%3Aterminal%3Adone");
  await page.locator("#root").evaluate(element => { element.style.height = "100vh"; });
  await expect(page.getByRole("complementary", { name: "图节点详情" })).toContainText("结束门禁");
  await expect(page.getByRole("complementary", { name: "图节点详情" })).toContainText("等待");
  for (const [status, label] of [["failed", "失败"], ["done", "已完成"], ["unknown", "未知"]]) {
    graph.nodes[3].status = status;
    await page.reload();
    await expect(gate).toContainText(label);
    await expect(page.getByRole("complementary", { name: "图节点详情" })).toContainText(label);
  }
  graph.nodes[2].status = "pending";
  await page.goto("/tests/inbox-workflow.html?surface=graph-refresh&request=A&node=workflow%3Astage%3Aprd-post-verify-discovery");
  await page.locator("#root").evaluate(element => { element.style.height = "100vh"; });
  await expect(discovery).toBeVisible();
  await expect(page.getByRole("complementary", { name: "图节点详情" })).toContainText("验证后扫描");
});

test("long goal expands exactly and resets when the workflow changes", async ({ page }) => {
  await setup(page);
  const longGoal = "交付😀可验证功能。".repeat(25);
  await page.route("**/api/projects/*/workflow-requests/*", route => {
    const id = new URL(route.request().url()).pathname.split("/").at(-1)!;
    return route.fulfill({ json: { result: request(id), requirement: { objective: id === "A" ? longGoal : "短目标" }, proposal: {}, artifacts: {}, lifecycle: {}, links: {} } });
  });
  await page.route("**/workflow-requests/*/graph/structure?*", route => route.fulfill({ status: 404 }));
  await page.route("**/workflow-requests/*/graph?*", route => route.fulfill({ json: full(new URL(route.request().url()).pathname.split("/").at(-2)!) }));
  await open(page);
  const goal = page.getByLabel("Goal context");
  await expect(goal.getByRole("button", { name: "展开目标" })).toHaveAttribute("aria-expanded", "false");
  await expect(goal.locator("strong")).not.toHaveText(longGoal);
  await goal.getByRole("button", { name: "展开目标" }).click();
  await expect(goal.locator("strong")).toHaveText(longGoal);
  await page.getByLabel("Select workflow").selectOption("B");
  await expect(goal.locator("strong")).toHaveText("短目标");
  await expect(goal.getByRole("button", { name: /目标/ })).toHaveCount(0);
  await page.getByLabel("Select workflow").selectOption("A");
  await expect(goal.getByRole("button", { name: "展开目标" })).toBeVisible();
  await expect(goal.locator("strong")).not.toHaveText(longGoal);
});

test("stage graph folds declared paths and opens observed execution details", async ({ page }, testInfo) => {
  await setup(page);
  await page.route("**/workflow-requests/A/graph/structure?*", route => route.fulfill({ status: 404 }));
  const stage = (id: string, status: string) => ({ id: `workflow:stage:${id}`, kind: "workflow", label: id, status,
    views: ["workflow"], data: { stage_id: id, roles: [{ name: "developer" }] } });
  const graph = { ...full("A"), nodes: [stage("scan", "completed"), stage("impl", "failed"),
    { id: "run:A", kind: "run", label: "run-A", status: "running", views: ["workflow"], data: {} },
    { id: "workflow:aggregate:scan", kind: "workflow", label: "aggregate", status: "completed", views: ["workflow"], data: {} },
    { id: "operation:impl-1", kind: "operation", label: "dev-lane-1", status: "running", views: ["workflow", "timeline"], data: { parent_stage_id: "impl", task_id: "TASK-1", role_instance: "dev-lane-1" } },
    { id: "attempt:one", kind: "attempt", label: "attempt one", status: "running", views: ["workflow"], data: {} }],
    edges: [{ id: "to-aggregate", source: "workflow:stage:scan", target: "workflow:aggregate:scan", relation: "aggregate", views: ["workflow"] },
      { id: "to-impl", source: "workflow:aggregate:scan", target: "workflow:stage:impl", relation: "triggers", views: ["workflow"] }] };
  await page.route("**/workflow-requests/A/graph?*", route => route.fulfill({ json: graph }));
  await open(page);
  const navigation = page.getByRole("navigation", { name: "Workflow graph views" });
  await expect(navigation.getByRole("button")).toHaveCount(3);
  await expect(navigation.getByLabel("更多视图", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Run 状态：执行中", { exact: true })).toHaveCount(0);
  await expect(page.locator('.react-flow__node[data-id^="workflow:stage:"]')).toHaveCount(2);
  await expect(page.locator(".react-flow__edge")).toHaveCount(2);
  await expect(page.getByText("折叠路径", { exact: true })).toBeVisible();
  await expect(page.locator('.react-flow__node[data-id="workflow:stage:impl"]')).toContainText("dev-lane-1");
  await page.locator('.react-flow__node[data-id="workflow:stage:impl"] header').click();
  const details = page.getByRole("complementary", { name: "图节点详情" });
  await expect(details).toContainText("已配置角色：developer");
  await expect(details).toContainText("已观测：1 个角色 · 1 条执行记录");
  await expect(details).toContainText("TASK-1");
  await expect(details).toContainText("具体原因尚未观测");
  await page.screenshot({ path: testInfo.outputPath("focused-workflow-graph.png") });
  await details.getByRole("button", { name: /执行中.*时间未观测/ }).click();
  await expect(details).toContainText("dev-lane-1");
  await expect(details.getByRole("link", { name: "在 Loop Timeline 中查看" })).toHaveAttribute("href", /expected_run=run-A/);
  await navigation.getByRole("button", { name: "任务 Graph" }).click();
  expect(new URL(page.url()).searchParams.get("graph")).toBe("modules");
  await page.reload();
  await expect(navigation.getByRole("button", { name: "任务 Graph" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("complementary", { name: "图节点详情" })).toContainText("dev-lane-1");
});

test("stage summary does not truncate large declared stage chains", async ({ page }) => {
  await setup(page);
  await page.route("**/workflow-requests/A/graph/structure?*", route => route.fulfill({ status: 404 }));
  const nodes = Array.from({ length: 48 }, (_, i) => ({ id: `workflow:stage:${i}`, kind: "workflow", label: `Stage ${i}`,
    status: "pending", views: ["workflow"], data: { stage_id: String(i) } }));
  const edges = nodes.slice(1).map((node, i) => ({ id: `edge:${i}`, source: nodes[i].id, target: node.id, relation: "next", views: ["workflow"] }));
  await page.route("**/workflow-requests/A/graph?*", route => route.fulfill({ json: { ...full("A"), nodes, edges } }));
  await open(page);
  await expect(page.locator(".sdlc-footer")).toContainText("48 个阶段/终态 · 47 条已显示关系");
  await expect(page.getByText(/仅显示 40 个重点节点/)).toHaveCount(0);
});

test("partial stage details load execution only on request", async ({ page }) => {
  await setup(page);
  const partial = structure("A");
  const node = { ...partial.nodes[0], data: { stage_id: "impl", roles: [{ name: "dev" }] } };
  partial.nodes = [node];
  let fullReads = 0;
  await page.route("**/workflow-requests/A/graph/structure?*", route => route.fulfill({ json: partial }));
  await page.route("**/workflow-requests/A/graph/node?*", route => route.fulfill({ json: { scope: scope("A", 10), node } }));
  await page.route("**/workflow-requests/A/graph?*", route => {
    fullReads++;
    const ops = [["finished", "settled"], ["running", "running"], ["blocked", "blocked"], ["cancelled", "cancelled"]].map(([id, status]) => ({ id: `operation:${id}`, kind: "operation", label: id, status, views: ["workflow"], data: { parent_stage_id: "impl", task_id: "TASK-1", role_instance: id } }));
    return route.fulfill({ json: { ...full("A"), nodes: [node, ...ops] } });
  });
  await open(page);
  await page.locator('.react-flow__node[data-id="workflow:stage:A"]').click();
  const details = page.getByRole("complementary", { name: "图节点详情" });
  await expect(details).toContainText("已配置角色：dev");
  await expect(details).toContainText("执行与 Task 详情尚未加载");
  expect(fullReads).toBe(0);
  await details.getByRole("button", { name: "加载阶段运行详情" }).click();
  await expect(details).toContainText("已观测：4 个角色 · 4 条执行记录");
  await expect(details.getByLabel("阶段执行摘要").locator("summary").first()).toContainText("blocked");
  await expect(details.getByLabel("阶段执行摘要").getByText("finished · TASK-1")).toBeVisible();
  expect(fullReads).toBe(1);
});

test("retired view links return to execution while preserving request and selected node", async ({ page }) => {
  await setup(page);
  await page.route("**/workflow-requests/A/graph/structure?*", route => route.fulfill({ status: 404 }));
  await page.route("**/workflow-requests/A/graph?*", route => route.fulfill({ json: full("A") }));
  for (const view of ["lifecycle", "trace", "delivery"]) {
    await page.goto(`/tests/inbox-workflow.html?surface=graph-refresh&request=A&graph=${view}&node=workflow%3Astage%3AA`);
    await page.locator("#root").evaluate(element => { element.style.height = "100vh"; });
    await expect(page.getByRole("button", { name: "执行 Graph" })).toHaveAttribute("aria-pressed", "true");
    await expect.poll(() => new URL(page.url()).searchParams.get("graph")).toBe("workflow");
    expect(new URL(page.url()).searchParams.get("request")).toBe("A");
    expect(new URL(page.url()).searchParams.get("node")).toBe("workflow:stage:A");
    await expect(page.getByRole("complementary", { name: "图节点详情" })).toContainText("workflow:stage:A");
    await expect(page.getByTestId("workflow-graph-canvas")).toBeVisible();
  }
  await page.goto('/tests/inbox-workflow.html?surface=graph-refresh&request=A&graph=modules');
  await expect(page.getByRole("button", { name: "任务 Graph" })).toHaveAttribute("aria-pressed", "true");
});

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

test("large task graphs preserve declared dependencies without arbitrary truncation", async ({ page }, testInfo) => {
  await setup(page);
  await page.route("**/workflow-requests/A/graph/structure?*", route => route.fulfill({ status: 404 }));
  const tasks = Array.from({ length: 120 }, (_, index) => ({ id: `task:${index}`, kind: "task", label: `Task ${index}`,
    status: index === 0 ? "failed" : "pending", views: ["modules"], data: {} }));
  const large = { ...full("A"), nodes: [{ id: "root", kind: "requirement", label: "Readable goal", status: "confirmed", views: ["modules"], data: {} }, ...tasks],
    edges: tasks.slice(1).map((node, index) => ({ id: `depends:${index}`, source: tasks[index].id, target: node.id, relation: "depends_on", views: ["modules"] })) };
  await page.route("**/workflow-requests/A/graph?*", route => route.fulfill({ json: large }));
  await open(page);
  await page.getByRole("button", { name: "任务 Graph" }).click();
  await expect(page.locator(".sdlc-footer")).toContainText("120 / 121 nodes · 119 relations");
  await expect(page.getByText(/仅显示 40 个重点节点/)).toHaveCount(0);
  const first = page.locator('.react-flow__node[data-id="task:0"]');
  await expect(first).toBeVisible();
  expect((await first.boundingBox())?.width).toBeGreaterThan(120);
  await page.screenshot({ path: testInfo.outputPath("large-task-graph.png") });
  await page.getByRole("button", { name: "Claim 覆盖" }).click();
  await expect(page.locator('.react-flow__node[data-id="root"]')).toBeVisible();
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
    await expect(page.getByText("Structure A", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Graph filters" }).click();
    await page.getByRole("button", { name: "展开角色、汇聚与事件结构" }).click();
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
  await expect(page.locator(".sdlc-plans")).toHaveCount(0);
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
  await expect(page.locator(".sdlc-footer")).toHaveAttribute("title", /Event 11/);
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

test("structure paints without Plan reads; full search appears after a bound load", async ({ page }) => {
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
    await expect(page.getByText("已显示流程结构；运行关系和计划按需读取。", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Structure A", { exact: true })).toBeVisible();
    await expect(page.locator(".sdlc-plans")).toHaveCount(0);
    expect(reads).toBe(0);
    await page.getByRole("button", { name: "Graph filters", exact: true }).click();
    await expect.poll(() => reads).toBe(1);
    await page.getByLabel("搜索图节点").fill("full-search-needle");
    await expect(page.getByText("正在读取完整运行关系…", { exact: true })).toBeVisible();
    await expect(page.getByText("No nodes match these filters.")).toHaveCount(0);
    release();
    await expect(page.getByText("Full task A", { exact: true })).toBeVisible();
    await expect(page.locator(".sdlc-plans")).toHaveCount(0);
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
  await page.getByRole("button", { name: "Graph filters" }).click();
  await expect(page.locator(".sdlc-plans")).toHaveCount(0);
  await expect.poll(() => details).toBe(2);
  await expect(page.locator(".sdlc-footer")).toHaveAttribute("title", /Event 11/);
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
  await page.getByRole("button", { name: "Graph filters" }).click();
  await expect(page.getByRole("alert")).toContainText("HTTP 409");
  expect(structures).toBe(2);
  expect(details).toBe(2);
  await expect(page.getByText("Structure A", { exact: true })).toBeVisible();
  await expect(page.locator(".sdlc-plans")).toHaveCount(0);
  allow = true;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.locator(".sdlc-plans")).toHaveCount(0);
  await expect.poll(() => details).toBe(3);
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
    await page.getByRole("button", { name: "Graph filters" }).click();
    await expect.poll(() => started).toBe(true);
    await page.getByLabel("Select workflow").selectOption("B");
    await expect(page.getByText("Structure B", { exact: true })).toBeVisible();
    release();
    await expect(page.getByText("Full task A", { exact: true })).toHaveCount(0);
    await expect(page.getByLabel("Select workflow")).toHaveValue("B");
  } finally { release(); }
});
