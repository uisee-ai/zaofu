import { expect, test, type Locator } from "@playwright/test";

const project = process.env.ZF_GRAPH_PROJECT_ID || "";
const requestId = process.env.ZF_GRAPH_REQUEST_ID || "";
test.skip(!project || !requestId, "Requires an explicitly selected isolated Workflow Run.");

async function selectView(workbench: Locator, name: string) {
  const values: Record<string, string> = { Lifecycle: "lifecycle", Task: "modules", Trace: "trace", Delivery: "delivery" };
  if (values[name]) await workbench.getByLabel("更多视图", { exact: true }).selectOption(values[name]);
  else await workbench.getByRole("button", { name: name === "Dag" ? "Graph" : name, exact: true }).click();
}

test("real Workflow graph shows bound execution, every lens, stable selection and mobile layout", async ({ page, request }) => {
  test.setTimeout(120_000);
  const endpoint = `/api/projects/${encodeURIComponent(project)}/workflow-requests/${encodeURIComponent(requestId)}/graph`;
  const response = await request.get(endpoint);
  expect(response.ok()).toBeTruthy();
  const graph = await response.json();
  expect(graph.scope.request_id).toBe(requestId);
  expect(graph.scope.execution_started).toBe(true);
  expect(graph.nodes.some((n: { kind: string }) => n.kind === "workflow")).toBeTruthy();
  expect(graph.nodes.some((n: { kind: string }) => n.kind === "operation")).toBeTruthy();
  await page.goto(`/?page=workflows&project=${encodeURIComponent(project)}&request=${encodeURIComponent(requestId)}`);
  const workbench = page.getByRole("region", { name: "Workflow 图工作台" });
  await expect(workbench).toBeVisible();
  await expect(workbench.getByTestId("workflow-graph-canvas")).toBeVisible({ timeout: 30_000 });
  for (const name of ["Lifecycle", "Task", "Dag", "Trace", "Delivery", "Timeline"]) {
    await selectView(workbench, name);
    await expect(workbench.getByTestId(name === "Timeline" ? "workflow-execution-timeline" : "workflow-graph-canvas")).toBeVisible();
    expect(new URL(page.url()).searchParams.has("graph")).toBeTruthy();
  }
  await selectView(workbench, "Dag");
  await workbench.getByRole("button", { name: "展开工作台", exact: true }).click();
  await workbench.getByRole("button", { name: /fit view/i }).click();
  await workbench.screenshot({ path: test.info().outputPath("workflow-execution.png") });
  await workbench.getByRole("button", { name: "Graph filters", exact: true }).click();
  await workbench.getByRole("button", { name: "展开角色、汇聚与事件结构", exact: true }).click();
  await workbench.getByText("Node directory", { exact: true }).click();
  await expect(workbench.getByRole("button", { name: "返回阶段与执行概览" })).toBeVisible();
  const task = graph.nodes.find((n: { kind: string }) => n.kind === "task");
  if (task) await workbench.getByLabel("搜索图节点").fill(task.id);
  const item = workbench.getByLabel("图节点目录").getByRole("button").first();
  await item.click();
  const selected = new URL(page.url()).searchParams.get("node");
  expect(selected).toBeTruthy();
  if (task) expect(selected).toBe(task.id);
  await workbench.getByLabel("搜索图节点").clear();
  for (const name of ["Task", "Trace", "Dag"]) {
    await selectView(workbench, name);
    await expect(workbench.getByRole("complementary", { name: "图节点详情" })).toContainText(selected!);
  }
  await page.reload();
  await expect(workbench.getByRole("complementary", { name: "图节点详情" })).toContainText(selected!, { timeout: 30_000 });
  const observed = graph.nodes.find((n: { kind: string; data: { agent_binding?: { source: string } } }) =>
    n.kind === "operation" && n.data.agent_binding?.source === "provider_usage");
  if (observed) {
    await workbench.getByRole("button", { name: "Graph filters", exact: true }).click();
    await workbench.getByText("Node directory", { exact: true }).click();
    await workbench.getByLabel("搜索图节点").fill(observed.id);
    await workbench.getByLabel("图节点目录").getByRole("button").first().click();
    await expect(workbench.getByRole("complementary", { name: "图节点详情" })).toContainText("实际用量记录");
    await expect(workbench.getByRole("complementary", { name: "图节点详情" })).toContainText(observed.data.agent_binding.model);
    await workbench.getByLabel("搜索图节点").clear();
  }
  if (graph.nodes.some((n: { kind: string; status: string }) => n.kind === "operation" && n.status === "running")) {
    await expect.poll(async () => (await (await request.get(endpoint)).json()).scope.source_seq,
      { timeout: 60_000, intervals: [2000] }).toBeGreaterThan(graph.scope.source_seq);
    await expect(workbench.locator(".sdlc-footer")).not.toHaveAttribute("title", `R${graph.scope.revision} · Event ${graph.scope.source_seq}`);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: test.info().outputPath("workflow-mobile.png"), fullPage: true });
  await test.info().attach("graph-source", { body: JSON.stringify(graph), contentType: "application/json" });
});

test("Plan reader retains unchanged attachments across graph polls and reloads changed bindings", async ({ page, request }) => {
  test.setTimeout(60_000);
  const endpoint = `/api/projects/${encodeURIComponent(project)}/workflow-requests/${encodeURIComponent(requestId)}/graph`;
  const graph = await (await request.get(endpoint)).json();
  const ports = ["task_map", "execution_spec", "source_inventory"];
  const plan = graph.plans.find((p: { artifacts?: { name: string }[] }) =>
    ports.every(name => p.artifacts?.some(a => a.name === name)));
  test.skip(!plan, "Requires a Plan with all three bound review ports.");
  let polls = 0;
  const loads: string[] = [];
  await page.route(`**${endpoint}*`, route => {
    polls += 1;
    return route.fulfill({ json: { ...graph, plans: [plan] } });
  });
  page.on("request", req => {
    if (req.method() === "GET" && ports.some(name => req.url().endsWith(`/plans/${plan.plan_id}/artifacts/${name}`))) loads.push(req.url());
  });
  await page.goto(`/?page=workflows&project=${project}&request=${requestId}`);
  await page.locator(".sdlc-plans summary").click();
  await page.getByRole("button", { name: `PRD / Plan Preview · ${plan.status}`, exact: true }).click();
  const reader = page.getByRole("region", { name: "计划审阅内容" });
  await expect(reader.getByRole("heading", { name: "本次交付与审批范围" })).toBeVisible({ timeout: 30_000 });
  const firstPoll = polls;
  await expect.poll(() => polls, { timeout: 20_000 }).toBeGreaterThanOrEqual(firstPoll + 3);
  expect(loads).toHaveLength(3);
  await expect(reader.getByText("正在读取同版规格…")).toHaveCount(0);
  // Only the graph response is a fixture; artifact reads still use the bound API.
  plan.artifacts.find((a: { name: string }) => a.name === "task_map").sha256 = "changed-binding-fixture";
  await expect.poll(() => loads.length, { timeout: 20_000 }).toBe(6);
  await expect(reader.getByRole("heading", { name: "本次交付与审批范围" })).toBeVisible();
});

test("Plan Chat fixture keeps a long quoted diff inside desktop and mobile panels", async ({ page, request }) => {
  test.setTimeout(90_000);
  const endpoint = `/api/projects/${encodeURIComponent(project)}/workflow-requests/${encodeURIComponent(requestId)}/graph`;
  const graph = await (await request.get(endpoint)).json();
  test.skip(!graph.plans.length, "Requires a bound Plan to test its review panel.");
  const plan = { ...graph.plans[0], status: "pending",
    artifacts: graph.plans[0].artifacts.filter((a: { name: string }) => a.name !== "accepted_plan") };
  const before = "这是待审阅的长需求段落，修改仅生成候选，不能直接批准执行。".repeat(20);
  const after = before + "按钮文案为归零。";
  await page.addInitScript(() => localStorage.setItem("zf.webActionToken", "intercepted-layout-fixture"));
  await page.route(`**${endpoint}*`, route => route.fulfill({ json: { ...graph, plans: [plan] } }));
  await page.route(`**/api/projects/${project}/plan-artifact-review`, async route => {
    const input = route.request().postDataJSON();
    expect(["context", "discuss"]).toContain(input.operation);
    expect(input.name).toBe(plan.artifacts[0].name);
    await route.fulfill({ json: input.operation === "context"
      ? { ok: true, body: before, digest: "fixture", editable: true }
      : { ok: true, answer: "局部修改候选", before, after, preview: graph.plans[0].markdown + "\n" + after, draft_ref: { ref: "fixture-only" } } });
  });
  await page.goto(`/?page=workflows&project=${project}&request=${requestId}`);
  await page.locator(".sdlc-plans summary").click();
  await page.getByRole("button", { name: "PRD / Plan Preview · pending", exact: true }).click();
  const source = page.getByTestId("plan-review-source");
  await expect(source).toHaveText(before, { timeout: 30_000 });
  await source.evaluate(element => {
    const range = document.createRange(); range.selectNodeContents(element);
    const selected = window.getSelection()!; selected.removeAllRanges(); selected.addRange(range);
    element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });
  await page.getByLabel("Plan artifact discussion").fill("仅修改引用片段");
  await page.getByRole("button", { name: "修改文档片段", exact: true }).click();
  const diff = page.getByTestId("plan-review-diff");
  await expect(diff).toContainText("按钮文案为归零");
  await expect(page.getByRole("button", { name: "确认计划并继续", exact: true })).toBeDisabled();
  for (const width of [1600, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await diff.scrollIntoViewIfNeeded();
    const size = await diff.boundingBox();
    expect(size!.width).toBeLessThanOrEqual(width);
    const panel = await page.getByRole("region", { name: "PRD Plan Agent Chat" }).boundingBox();
    expect(size!.width).toBeLessThanOrEqual(panel!.width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
    await page.screenshot({ path: test.info().outputPath(`plan-diff-fixture-${width}.png`) });
  }
  await page.getByRole("button", { name: "放弃修改", exact: true }).last().click();
  await expect(diff).toHaveCount(0);
});

test("explicit graph filters compact the layout while polls retain the viewport", async ({ page, request }) => {
  const endpoint = `/api/projects/${encodeURIComponent(project)}/workflow-requests/${encodeURIComponent(requestId)}/graph`;
  const graph = await (await request.get(endpoint)).json();
  let polls = 0;
  // Display-only fixture: separate active nodes with a long history column.
  const nodes = Array.from({ length: 32 }, (_, index) => ({
    id: `layout-${String(index).padStart(2, "0")}`, kind: "operation", label: `Layout fixture ${index}`,
    status: index === 0 || index === 31 ? "running" : "done", views: ["workflow"], data: {},
  }));
  await page.route(`**${endpoint}*`, route => {
    polls += 1;
    return route.fulfill({ json: { ...graph, nodes, edges: [] } });
  });
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.goto(`/?page=workflows&project=${project}&request=${requestId}&graph=workflow`);
  const workbench = page.getByRole("region", { name: "Workflow 图工作台" });
  await workbench.getByRole("button", { name: "展开工作台", exact: true }).click();
  await workbench.getByRole("button", { name: "Graph filters", exact: true }).click();
  await workbench.getByText("Node directory", { exact: true }).click();
  await expect(workbench.getByLabel("图节点目录").getByRole("button")).toHaveCount(32);
  await workbench.getByLabel("图节点状态").selectOption("running");
  const active = workbench.locator('.react-flow__node[data-id="layout-31"]');
  await expect(active).toBeVisible();
  // Auto-fit must make the filtered cards legible without a manual fit click.
  await expect.poll(async () => (await active.boundingBox())?.width || 0).toBeGreaterThan(160);
  const first = workbench.locator('.react-flow__node[data-id="layout-00"]');
  const a = (await first.boundingBox())!, b = (await active.boundingBox())!;
  expect(Math.abs(a.y - b.y)).toBeLessThan(350);
  await workbench.getByRole("button", { name: /zoom out/i }).click();
  const viewport = workbench.locator(".react-flow__viewport");
  // Wait for the zoom control animation before checking polling stability.
  await page.waitForTimeout(500);
  const transform = await viewport.getAttribute("style");
  const before = polls;
  await expect.poll(() => polls, { timeout: 12_000 }).toBeGreaterThanOrEqual(before + 2);
  expect(await viewport.getAttribute("style")).toBe(transform);
  await workbench.getByLabel("搜索图节点").fill("layout-31");
  await expect(workbench.getByLabel("图节点目录").getByRole("button")).toHaveCount(1);
  await expect.poll(async () => (await active.boundingBox())?.width || 0).toBeGreaterThan(160);
});

test("completed fanout updates aggregate and role structure nodes", async ({ page, request }) => {
  const stageId = process.env.ZF_GRAPH_EXPECT_COMPLETED_STAGE || "";
  test.skip(!stageId, "Requires an explicitly selected completed fanout stage.");
  const graph = await (await request.get(`/api/projects/${encodeURIComponent(project)}/workflow-requests/${encodeURIComponent(requestId)}/graph`)).json();
  const aggregate = graph.nodes.find((n: { id: string }) => n.id === `workflow:aggregate:${stageId}`);
  expect(aggregate.status).toBe("completed");
  expect(aggregate.data.status_source_event_id).toBeTruthy();
  const roles = aggregate.data.definition.roles as string[];
  expect(roles.length).toBeGreaterThan(0);
  for (const role of roles) {
    const node = graph.nodes.find((n: { id: string }) => n.id === `workflow:role:${role}`);
    expect(node.status).toBe("settled");
    expect(node.data.current_operation_id).toBeTruthy();
  }
  const eventType = process.env.ZF_GRAPH_EXPECT_REPORTED_EVENT || "";
  if (eventType) {
    const trigger = graph.nodes.find((n: { id: string }) => n.id === `workflow:event:${eventType}`);
    expect(trigger.status).toBe("reported");
    expect(trigger.data.source_event_ids.length).toBeGreaterThan(0);
  }
  await page.goto(`/?page=workflows&project=${project}&request=${requestId}`);
  const workbench = page.getByRole("region", { name: "Workflow 图工作台" });
  await expect(workbench).toBeVisible();
  await selectView(workbench, "Dag");
  await workbench.getByRole("button", { name: "展开工作台", exact: true }).click();
  await workbench.getByRole("button", { name: "Graph filters", exact: true }).click();
  await workbench.getByRole("button", { name: "展开角色、汇聚与事件结构", exact: true }).click();
  await workbench.getByText("Node directory", { exact: true }).click();
  const checks = [[aggregate.id, "已结束"], [`workflow:role:${roles[0]}`, "操作结束"]];
  if (eventType) checks.push([`workflow:event:${eventType}`, "已报告"]);
  for (const [id, label] of checks) {
    await workbench.getByLabel("搜索图节点").fill(id);
    await workbench.getByLabel("图节点目录").getByRole("button", { exact: false }).first().click();
    const inspector = workbench.getByRole("complementary", { name: "图节点详情" });
    await expect(inspector).toContainText(id);
    await expect(inspector).toContainText(label);
    await workbench.screenshot({ path: test.info().outputPath(id === aggregate.id ? "aggregate-completed.png" : id.startsWith("workflow:event:") ? "trigger-reported.png" : "role-settled.png") });
  }
});

test("terminal Run, Tasks and Goal coverage agree with kernel evidence", async ({ page, request }) => {
  test.skip(process.env.ZF_GRAPH_EXPECT_TERMINAL !== "1", "Requires a completed isolated acceptance Run.");
  test.setTimeout(90_000);
  const endpoint = `/api/projects/${encodeURIComponent(project)}/workflow-requests/${encodeURIComponent(requestId)}/graph`;
  const graph = await (await request.get(endpoint)).json();
  const run = graph.nodes.find((n: { kind: string }) => n.kind === "run");
  expect(run.status).toBe("done");
  const terminal = graph.nodes.find((n: { id: string }) => n.id === `event:${run.data.terminal_event_id}`);
  expect(terminal.data.event.origin).toBe("kernel");
  expect(terminal.data.event.type).toBe("run.goal.completed");
  const tasks = graph.nodes.filter((n: { kind: string }) => n.kind === "task");
  expect(tasks.length).toBeGreaterThan(0);
  for (const task of tasks) {
    expect(task.status).toBe("done");
    expect(task.data.accepted_event_ids.length).toBeGreaterThan(0);
  }
  expect(graph.goal_coverage.summary.closed_claims).toBe(graph.goal_coverage.summary.mandatory_claims);
  expect(graph.goal_coverage.summary.closed_claims).toBeGreaterThan(0);
  expect(graph.goal_coverage.summary.open_gaps).toBe(0);
  expect(graph.capabilities.production.status).toBe("unconnected");
  await page.goto(`/?page=workflows&project=${project}&request=${requestId}`);
  const workbench = page.getByRole("region", { name: "Workflow 图工作台" });
  await expect(workbench).toBeVisible({ timeout: 30_000 });
  await workbench.getByRole("button", { name: "展开工作台", exact: true }).click();
  for (const [view, file] of [["Task", "modules-terminal.png"], ["Dag", "workflow-terminal.png"], ["Delivery", "delivery-terminal.png"]]) {
    await selectView(workbench, view);
    await workbench.getByRole("button", { name: /fit view/i }).click();
    await workbench.screenshot({ path: test.info().outputPath(file) });
  }
  await workbench.getByRole("button", { name: "Graph filters", exact: true }).click();
  await workbench.getByText("Node directory", { exact: true }).click();
  await workbench.getByLabel("图节点状态").selectOption("done");
  await workbench.getByLabel("搜索图节点").fill(`run:${requestId}`);
  await workbench.getByLabel("图节点目录").getByRole("button").first().click();
  await expect(workbench.getByRole("complementary", { name: "图节点详情" })).toContainText("已完成");
  await workbench.screenshot({ path: test.info().outputPath("goal-terminal-inspector.png") });
  await test.info().attach("terminal-graph", { body: JSON.stringify(graph), contentType: "application/json" });
});
