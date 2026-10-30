import { expect, test, type APIRequestContext, type Page, type Locator } from "@playwright/test";

const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST || "";
test.skip(!token, "Requires workflow-prepare-server.py with isolated state and mocked providers.");
test.describe.configure({ mode: "serial", timeout: 90_000 });
const views = ["Task", "Dag", "Timeline"];

async function selectView(workbench: Locator, name: string) {
  if (name === "Task") await workbench.getByRole("button", { name: "任务 Graph", exact: true }).click();
  else await workbench.getByRole("button", { name: name === "Dag" ? "执行 Graph" : name, exact: true }).click();
}

async function prepare(request: APIRequestContext, id: string) {
  const response = await request.post("/api/projects/default/actions/workflow-request", {
    headers: { "x-zf-web-token": token },
    data: { project_id: "default", actor: "playwright", idempotency_key: id,
      payload: { request_id: id, kind: "issue", objective: `Graph browser fixture ${id}`, backend: "mock", allow_missing_env: true,
        acceptance: ["Active sessions remain valid.", "Expired sessions fail."], constraints: ["Preserve API."] } },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  await expect.poll(async () => (await (await request.get(`/api/projects/default/workflow-requests/${id}`)).json()).result.status).toBe("proposed");
  return (await request.get(`/api/projects/default/workflow-requests/${id}`)).json();
}
async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
}
test.beforeEach(async ({ page }) => {
  await page.addInitScript(value => { localStorage.setItem("zf.webActionToken", value); localStorage.setItem("zf.themeMode", "light"); }, token);
});

test("graph views, themed cards and live projection updates preserve viewport", async ({ page, request }) => {
  const id = `graph-theme-${Date.now()}`;
  await prepare(request, id);
  const endpoint = `/api/projects/default/workflow-requests/${id}/graph`;
  const actual = await (await request.get(endpoint)).json();
  // Explicit display fixture, not evidence of actual agent execution.
  const graph = { ...actual, plans: [], nodes: [
    { id: "goal", kind: "requirement", label: "Build a racing game", status: "running", data: {} },
    { id: "audit", kind: "operation", label: "Audit game controls", status: "done", data: { agent_binding: { backend: "claude-code", source: "provider_usage" } } },
    { id: "drive", kind: "operation", label: "Build driving physics", status: "running", data: { agent_binding: { backend: "codex", source: "provider_usage" } } },
    { id: "track", kind: "operation", label: "Build the race track", status: "running", data: { agent_binding: { backend: "claude-code", source: "provider_usage" } } },
    { id: "verify", kind: "operation", label: "Verify gameplay", status: "pending", data: { agent_binding: { backend: "opencode", source: "provider_usage" } } },
    { id: "delivery", kind: "operation", label: "Integration pass", status: "pending", data: { agent_binding: { backend: "codex", source: "provider_usage" } } },
  ].map(n => ({ ...n, views: ["lifecycle", "modules", "workflow", "trace", "delivery", "timeline"] })),
  edges: [["goal", "audit"], ["goal", "drive"], ["goal", "track"], ["audit", "verify"], ["drive", "verify"], ["track", "delivery"], ["verify", "delivery"]].map(([source, target]) => ({ id: `${source}-${target}`, source, target, relation: "depends_on", views: ["lifecycle", "modules", "workflow", "trace", "delivery", "timeline"] })) };
  let polls = 0;
  await page.route(`**/api/projects/*/workflow-requests/${id}/graph*`, route => { polls++; return route.fulfill({ json: graph }); });
  await page.setViewportSize({ width: 1600, height: 1100 });
  await page.goto(`/?page=workflows&project=default&request=${id}`);
  const workbench = page.getByRole("region", { name: "Workflow 图工作台" });
  const canvas = workbench.getByTestId("workflow-graph-canvas");
  await expect(canvas).toBeVisible();
  await expect(page.getByRole("tab", { name: /Needs decision|Active|History/ })).toHaveCount(0);
  if (await page.getByLabel("Select workflow").count()) await expect(page.getByLabel("Select workflow")).toHaveValue(id);
  await expect(page.getByRole("heading", { name: "Workflow", exact: true })).toHaveCount(1);
  await expect(page.locator(".workflow-proposal-toolbar")).not.toContainText("Graph browser fixture");
  await expect(page.locator(".workflow-context-bar,.workflow-selection-meta")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Review & Approve", exact: true })).toHaveCount(0);
  const navigation = workbench.getByRole("navigation", { name: "Workflow graph views" });
  await expect(navigation.locator("svg")).toHaveCount(0);
  const tabBounds = await navigation.getByRole("button").evaluateAll(buttons => buttons.map(b => b.getBoundingClientRect().top));
  expect(Math.max(...tabBounds) - Math.min(...tabBounds)).toBeLessThan(2);
  await expect(workbench.getByText("版本与数据可用性", { exact: true })).toHaveCount(0);
  await expect(workbench.getByText("Live Events", { exact: true })).toHaveCount(0);
  for (const name of views) {
    await selectView(workbench, name);
    if (name === "Timeline") { await expect(workbench.getByTestId("workflow-execution-timeline")).toBeVisible(); continue; }
    await expect(canvas).toBeVisible();
    await expect(workbench.locator(".sdlc-caption")).toHaveCount(0);
    await expect(workbench.getByText(/下游高亮表示已声明关系可达|发布：未知|未绑定部署环境与生产健康数据源/)).toHaveCount(0);
    const frame = (await canvas.boundingBox())!;
    const region = (await workbench.boundingBox())!;
    expect(frame.width).toBeGreaterThan(region.width - 40);
    expect(frame.height).toBeGreaterThan(600);
    expect(frame.y + frame.height).toBeLessThanOrEqual(1100);
  }
  await selectView(workbench, "Dag");
  await expect.poll(async () => {
    const bounds = (await canvas.boundingBox())!;
    return 1100 - bounds.y - bounds.height;
  }).toBeLessThan(70);
  const drive = workbench.locator('.react-flow__node[data-id="drive"]');
  await expect(drive).toContainText("running");
  await expect(workbench.locator('.react-flow__node[data-id="audit"] .sdlc-provider-claude')).toBeVisible();
  await expect(workbench.locator('.react-flow__node[data-id="goal"]')).toBeVisible();
  const goal = (await workbench.locator('.react-flow__node[data-id="goal"]').boundingBox())!;
  expect((await drive.boundingBox())!.x).toBeGreaterThan(goal.x + goal.width);
  const light = await canvas.evaluate(el => getComputedStyle(el).backgroundColor);
  expect(light).toBe(await page.locator(".workflow-proposal-page").evaluate(el => getComputedStyle(el).backgroundColor));
  await workbench.screenshot({ path: test.info().outputPath("workflow-light.png") });
  await page.evaluate(() => document.documentElement.dataset.theme = "dark");
  const dark = await canvas.evaluate(el => getComputedStyle(el).backgroundColor);
  expect(dark).not.toBe(light);
  expect(dark).toBe(await page.locator(".workflow-proposal-page").evaluate(el => getComputedStyle(el).backgroundColor));
  await workbench.screenshot({ path: test.info().outputPath("workflow-dark.png") });
  await workbench.getByRole("button", { name: "展开工作台", exact: true }).click();
  const expanded = (await workbench.boundingBox())!;
  expect(expanded.x).toBeGreaterThanOrEqual(0);
  expect(expanded.x + expanded.width).toBeLessThanOrEqual(1600);
  expect(expanded.y + expanded.height).toBeLessThanOrEqual(1100);
  await workbench.getByRole("button", { name: "收回工作台", exact: true }).click();
  await workbench.getByRole("button", { name: /fit view/i }).click();
  await expect.poll(async () => (await drive.boundingBox())!.width).toBeLessThanOrEqual(209);
  await workbench.getByRole("button", { name: /zoom out/i }).click();
  await page.waitForTimeout(400);
  const viewport = workbench.locator(".react-flow__viewport");
  const transform = await viewport.getAttribute("style");
  graph.nodes.find(n => n.id === "drive")!.status = "done";
  const before = polls;
  // No source event was emitted by this display-only fixture; the 30s
  // reconciliation tick must still pick it up without resetting the viewport.
  await expect.poll(() => polls, { timeout: 35_000 }).toBeGreaterThan(before);
  await expect(drive).toContainText("done");
  expect(await viewport.getAttribute("style")).toBe(transform);
  expect(await workbench.locator('.react-flow__edge[data-id="goal-drive"]').count()).toBe(1);
  await drive.click();
  await expect(workbench.getByLabel("图节点详情")).toContainText("已完成");
  await selectView(workbench, "Task");
  await expect(workbench.getByLabel("图节点详情")).toContainText("Build driving physics");
  await page.reload();
  await expect(workbench.getByLabel("图节点详情")).toContainText("Build driving physics");
  await workbench.getByRole("button", { name: "Close node details" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow(page);
  await expect.poll(async () => (await canvas.locator(".react-flow__node").first().boundingBox())?.width || 0).toBeGreaterThan(160);
  await workbench.getByRole("button", { name: /fit view/i }).click();
  await expect.poll(async () => {
    const bounds = (await canvas.boundingBox())!;
    const first = await workbench.locator('.react-flow__node[data-id="goal"]').boundingBox();
    const last = await workbench.locator('.react-flow__node[data-id="delivery"]').boundingBox();
    return Boolean(first && last && first.y >= bounds.y && last.y + last.height <= bounds.y + bounds.height);
  }).toBeTruthy();
  await page.screenshot({ path: test.info().outputPath("workflow-mobile.png"), fullPage: true });
});


test("large graphs fit shorter windows and Timeline remains available", async ({ page, request }) => {
  const id = `graph-resize-${Date.now()}`;
  await prepare(request, id);
  const actual = await (await request.get(`/api/projects/default/workflow-requests/${id}/graph`)).json();
  const nodes = Array.from({ length: 40 }, (_, index) => ({ id: `wide-${index}`, kind: "operation", label: `Parallel operation ${index}`, status: "pending", data: {}, views: ["workflow", "timeline"] }));
  await page.route(`**/api/projects/*/workflow-requests/${id}/graph*`, route => route.fulfill({ json: { ...actual, nodes, edges: [], plans: [] } }));
  await page.route(`**/api/projects/*/workflow-requests/${id}/graph/structure?*`, route => route.fulfill({ status: 404 }));
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto(`/?page=workflows&project=default&request=${id}`);
  const workbench = page.getByRole("region", { name: "Workflow 图工作台" });
  const canvas = workbench.getByTestId("workflow-graph-canvas");
  const assertFits = async () => {
    await expect.poll(() => canvas.evaluate(element => {
      const frame = element.getBoundingClientRect();
      const nodes = [...element.querySelectorAll(".react-flow__node")].map(node => node.getBoundingClientRect());
      const panel = document.querySelector(".projection-scroll")!.getBoundingClientRect();
      const page = document.querySelector(".workflow-graph-page")!.getBoundingClientRect();
      return nodes.length === 40 && frame.height > 0 && frame.bottom <= innerHeight && page.bottom <= panel.bottom + 1
        && nodes.every(node => node.left >= frame.left && node.right <= frame.right && node.top >= frame.top && node.bottom <= frame.bottom);
    }), { timeout: 10_000 }).toBeTruthy();
  };
  await expect.poll(async () => (await canvas.locator(".react-flow__node").first().boundingBox())?.width || 0).toBeGreaterThan(150);
  await workbench.getByRole("button", { name: /fit view/i }).click();
  await assertFits();
  await page.setViewportSize({ width: 1280, height: 480 });
  await workbench.getByRole("button", { name: /fit view/i }).click();
  await assertFits();
  await page.evaluate(() => { (document.querySelector(".board-panel") as HTMLElement).style.height = "340px"; });
  await workbench.getByRole("button", { name: /fit view/i }).click();
  await assertFits();
  await page.evaluate(() => { (document.querySelector(".board-panel") as HTMLElement).style.height = ""; });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(async () => (await canvas.locator(".react-flow__node").first().boundingBox())?.width || 0).toBeGreaterThan(160);
  await workbench.getByRole("button", { name: /fit view/i }).click();
  await assertFits();
  await noOverflow(page);
  await workbench.getByRole("button", { name: "Timeline", exact: true }).click();
  await expect(workbench.getByRole("heading", { name: "执行区间与关键路径" })).toHaveCount(0);
  await expect(workbench.getByText("时序或依赖证据尚不完整，暂不计算关键路径。", { exact: true })).toHaveCount(0);
  await expect(workbench.getByRole("table")).toHaveCount(0);
  await expect(workbench.getByTestId("workflow-execution-timeline")).toBeVisible();
});

test("Agent dialog retains conversation and pending diff while Raw stays focused on YAML", async ({ page, request }) => {
  const id = `graph-raw-${Date.now()}`;
  const before = await prepare(request, id);
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.goto(`/?page=workflows&project=default&request=${id}`);
  await expect(page.getByRole("button", { name: "Agent", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Raw", exact: true }).click();
  const raw = page.getByRole("complementary", { name: "Raw YAML", exact: true });
  await expect(raw.locator("pre")).toContainText("kind: IssueFlow");
  await expect(page.getByTestId("workflow-preview-review")).toBeHidden();
  await expect.poll(async () => page.getByTestId("workflow-graph-canvas").evaluate(canvas => {
    const frame = canvas.getBoundingClientRect();
    const nodes = [...canvas.querySelectorAll(".react-flow__node")].map(n => n.getBoundingClientRect());
    return nodes.length > 0 && nodes.every(n => n.left >= frame.left && n.right <= frame.right && n.top >= frame.top && n.bottom <= frame.bottom);
  })).toBeTruthy();
  await expect(raw.getByRole("button", { name: "Edit with Agent", exact: true })).toHaveCount(0);
  await expect(raw.getByRole("button", { name: "Review & Approve", exact: true })).toHaveCount(0);
  await raw.getByRole("button", { name: "Agent", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Workflow Agent", exact: true });
  const chat = dialog.getByTestId("workflow-preview-review");
  await expect(raw.getByRole("button", { name: "Details", exact: true })).toHaveCount(0);
  expect((await dialog.boundingBox())!.height).toBeLessThan(260);
  expect((await dialog.boundingBox())!.width).toBeLessThan((await raw.boundingBox())!.width);
  await expect(raw.locator(".workflow-raw-yaml pre")).toBeInViewport();
  await expect(dialog.getByRole("textbox")).toHaveCount(1);
  await expect(chat.getByLabel("Preview discussion")).toBeFocused();
  await expect(raw.getByTestId("workflow-preview-review")).toBeVisible();
  await expect(dialog.locator(".workflow-agent-composer")).toBeInViewport();
  await dialog.screenshot({ path: test.info().outputPath("agent-empty.png") });
  const light = await dialog.evaluate(element => getComputedStyle(element).backgroundColor);
  await page.evaluate(() => document.documentElement.dataset.theme = "dark");
  expect(await dialog.evaluate(element => getComputedStyle(element).backgroundColor)).not.toBe(light);
  await dialog.screenshot({ path: test.info().outputPath("agent-dark.png") });
  await page.evaluate(() => document.documentElement.dataset.theme = "light");
  await raw.locator(".workflow-raw-yaml pre").click();
  await expect(dialog).toBeHidden();
  await raw.getByRole("button", { name: "Agent", exact: true }).click();
  await chat.getByLabel("Preview discussion").fill("Why is this needed?");
  await chat.getByLabel("Preview discussion").press("Control+Enter");
  await expect(chat.getByTestId("review-answer")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await raw.getByRole("button", { name: "Agent", exact: true }).click();
  await expect(chat.getByTestId("review-answer")).toBeVisible();
  await expect(chat.getByLabel("Review field")).toHaveCount(0);
  await page.screenshot({ path: test.info().outputPath("raw-desktop.png"), fullPage: true });
  await raw.locator(".workflow-raw-yaml pre").evaluate(node => {
    const start = node.textContent!.indexOf("lanes: 1");
    const range = document.createRange(); range.setStart(node.firstChild!, start); range.setEnd(node.firstChild!, start + "lanes: 1".length);
    const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range);
    node.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });
  await page.getByRole("button", { name: "引用讨论 / 修改", exact: true }).click();
  await expect(chat.getByTestId("review-quote")).toHaveText("lanes: 1");
  await chat.getByLabel("Preview discussion").fill("Use two implementation lanes.");
  const discussion = page.waitForRequest(req => req.url().endsWith("/workflow-preview-review") && req.postDataJSON()?.operation === "discuss");
  await chat.getByRole("button", { name: "Send", exact: true }).click();
  expect((await discussion).postDataJSON().message).toContain("lanes: 1");
  await expect(chat.getByTestId("review-diff")).toContainText("2");
  await dialog.screenshot({ path: test.info().outputPath("agent-diff.png") });
  await dialog.getByRole("button", { name: "Close agent chat" }).click();
  await expect(dialog).toBeHidden();
  await raw.getByRole("button", { name: "Agent", exact: true }).click();
  await expect(chat.getByTestId("review-diff")).toContainText("2");
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow(page);
  await expect(dialog.getByRole("textbox")).toBeInViewport();
  await expect(dialog.getByRole("button", { name: "Send", exact: true })).toBeInViewport();
  await dialog.screenshot({ path: test.info().outputPath("agent-mobile.png") });
  await page.setViewportSize({ width: 1600, height: 1000 });
  await expect(page.getByTestId("workflow-start")).toBeDisabled();
  if (await page.getByLabel("Select workflow").count()) await expect(page.getByLabel("Select workflow")).toBeDisabled();
  await expect(page.getByRole("button", { name: "Close workflow panel" })).toBeDisabled();
  await chat.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(raw.locator(".workflow-raw-yaml pre")).toContainText("lanes: 2");
  await expect(chat).toBeVisible();
  await expect(chat.getByTestId("review-diff")).toHaveCount(0);
  const after = await (await request.get(`/api/projects/default/workflow-requests/${id}`)).json();
  expect(after.result.revision).toBeGreaterThan(before.result.revision);
  expect(after.lifecycle.submitted).toBe(false);
  await dialog.getByRole("button", { name: "Close agent chat" }).click();
  await page.getByRole("button", { name: "Close workflow panel" }).click();
  await expect(page.getByRole("button", { name: "Agent", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Raw", exact: true }).click();
  await raw.getByRole("button", { name: "Agent", exact: true }).click();
  await expect(chat.getByText("已更新 YAML。", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "Close agent chat" }).click();
  const start = page.getByTestId("workflow-start");
  await expect(start).toBeEnabled();
  const calls: string[] = [];
  page.on("request", req => { if (req.method() === "POST" && /actions\/workflow-(config-apply|submit)$/.test(req.url())) calls.push(req.url().split("/").at(-1)!); });
  await page.route("**/actions/workflow-config-apply", route => route.fulfill({ status: 409,
    json: { ok: false, status: "stale", action: "workflow-config-apply", reason: "Fixture CAS conflict" } }));
  await start.click();
  await expect(page.getByTestId("workflow-proposal-feedback")).toContainText("Fixture CAS conflict");
  await expect(start).toBeEnabled();
  expect(calls).toEqual(["workflow-config-apply"]);
  await expect(chat).toBeHidden();
  await page.unroute("**/actions/workflow-config-apply");
  calls.length = 0;
  await start.click();
  await expect(raw.getByText("This run uses an immutable snapshot.", { exact: false })).toBeVisible();
  expect(calls).toEqual(["workflow-config-apply", "workflow-submit"]);
  await page.getByRole("button", { name: "Close workflow panel" }).click();
  await page.getByRole("button", { name: "Raw", exact: true }).click();
  await expect(raw.getByText("This run uses an immutable snapshot.", { exact: false })).toBeVisible();
  await raw.getByRole("button", { name: "Agent", exact: true }).click();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByTestId("workflow-agent-availability")).toContainText("已启动");
  await expect(dialog.getByRole("textbox")).toBeEnabled();
  const frozenYaml = await raw.locator("pre").textContent();
  await dialog.getByRole("textbox").fill("Explain the implementation lanes in this running workflow.");
  await dialog.getByRole("button", { name: "Send", exact: true }).click();
  await expect(dialog.getByTestId("review-answer").last()).toContainText("冻结配置");
  await expect(dialog.getByRole("button", { name: "Apply", exact: true })).toHaveCount(0);
  await expect(raw.locator("pre")).toHaveText(frozenYaml!);
  expect(calls).toEqual(["workflow-config-apply", "workflow-submit"]);
  await dialog.getByRole("button", { name: "Close agent chat" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow(page);
  await page.screenshot({ path: test.info().outputPath("raw-mobile.png"), fullPage: true });
});

test("request selection retains deep links without showing Plan documents", async ({ page, request }) => {
  const id = `graph-request-${Date.now()}`;
  const other = `${id}-other`;
  await prepare(request, id);
  await prepare(request, other);
  await page.goto(`/?page=workflows&project=default&request=${id}`);
  await expect(page.getByLabel("Select workflow")).toHaveValue(id);
  await page.getByLabel("Select workflow").selectOption(other);
  await expect(page).toHaveURL(new RegExp(`request=${other}`));
  await expect(page.locator(".sdlc-plans")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Workflow PRD Plan Preview" })).toHaveCount(0);
  await page.getByLabel("Select workflow").selectOption(id);
  await expect(page).toHaveURL(new RegExp(`request=${id}`));
});
