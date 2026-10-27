import { expect, test, type APIRequestContext, type Page, type Locator } from "@playwright/test";

const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST || "";
test.skip(!token, "Requires workflow-prepare-server.py with isolated state and mocked providers.");
test.describe.configure({ mode: "serial", timeout: 90_000 });
const views = ["Lifecycle", "Task", "Dag", "Trace", "Delivery", "Timeline"];

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
async function selectText(element: Locator) {
  await element.scrollIntoViewIfNeeded();
  await element.evaluate(node => {
    const range = document.createRange(); range.selectNodeContents(node);
    const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range);
  });
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(value => { localStorage.setItem("zf.webActionToken", value); localStorage.setItem("zf.themeMode", "light"); }, token);
});

test("six graph views, themed top-down cards and live projection updates preserve viewport", async ({ page, request }) => {
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
    await workbench.getByRole("button", { name, exact: true }).click();
    await expect(canvas).toBeVisible();
    await expect(workbench.locator(".sdlc-caption")).toHaveCount(0);
    await expect(workbench.getByText(/下游高亮表示已声明关系可达|发布：未知|未绑定部署环境与生产健康数据源/)).toHaveCount(0);
    const frame = (await canvas.boundingBox())!;
    const region = (await workbench.boundingBox())!;
    expect(frame.width).toBeGreaterThan(region.width - 40);
    expect(frame.height).toBeGreaterThan(600);
    expect(frame.y + frame.height).toBeLessThanOrEqual(1100);
  }
  await workbench.getByRole("button", { name: "Dag", exact: true }).click();
  await expect.poll(async () => {
    const bounds = (await canvas.boundingBox())!;
    return 1100 - bounds.y - bounds.height;
  }).toBeLessThan(70);
  const drive = workbench.locator('.react-flow__node[data-id="drive"]');
  await expect(drive).toContainText("running");
  await expect(workbench.locator('.react-flow__node[data-id="audit"] .sdlc-provider-claude')).toBeVisible();
  await expect(workbench.locator('.react-flow__node[data-id="goal"]')).toBeVisible();
  const goal = (await workbench.locator('.react-flow__node[data-id="goal"]').boundingBox())!;
  expect((await drive.boundingBox())!.y).toBeGreaterThan(goal.y + goal.height);
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
  await workbench.getByRole("button", { name: "Task", exact: true }).click();
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


test("large graphs fit shorter windows and resized content panels; Timeline stays graph-only", async ({ page, request }) => {
  const id = `graph-resize-${Date.now()}`;
  await prepare(request, id);
  const actual = await (await request.get(`/api/projects/default/workflow-requests/${id}/graph`)).json();
  const nodes = Array.from({ length: 40 }, (_, index) => ({ id: `wide-${index}`, kind: "operation", label: `Parallel operation ${index}`, status: "pending", data: {}, views: ["workflow", "timeline"] }));
  await page.route(`**/api/projects/*/workflow-requests/${id}/graph*`, route => route.fulfill({ json: { ...actual, nodes, edges: [], plans: [] } }));
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
  await assertFits();
  await page.setViewportSize({ width: 1280, height: 480 });
  await assertFits();
  await page.evaluate(() => { (document.querySelector(".board-panel") as HTMLElement).style.height = "340px"; });
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
  await expect.poll(async () => (await canvas.locator(".react-flow__node").first().boundingBox())?.width || 0).toBeGreaterThan(160);
  await workbench.getByRole("button", { name: /fit view/i }).click();
  await assertFits();
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

test("request selection retains deep links and Plan review keeps chat and approval bound", async ({ page, request }) => {
  const id = `graph-plan-${Date.now()}`;
  await prepare(request, id);
  const other = `graph-other-${Date.now()}`;
  await prepare(request, other);
  const graph = await (await request.get(`/api/projects/default/workflow-requests/${id}/graph`)).json();
  // Render and action-binding fixture; this does not claim Planner or Kernel execution.
  const plan = { plan_id: "plan-ui-fixture", status: "pending", markdown: "# Racing game plan\n\nKeep the race duration at 90 seconds.",
    actions: { approve: "plan-approve" }, action_payload: { binding_digest: "fixture-plan-version" },
    artifacts: [{ name: "accepted_plan", ref: "fixtures/plan.md", sha256: "fixture-digest" }] };
  let approvals = 0;
  let revisions = 0;
  await page.route(`**/api/projects/*/workflow-requests/${id}/graph*`, route => route.fulfill({ json: { ...graph, plans: [plan] } }));
  await page.route("**/plan-artifact-review", route => {
    const payload = route.request().postDataJSON();
    if (payload.operation === "apply") {
      expect(payload.draft_ref.ref).toBe("fixture-candidate");
      revisions++;
      plan.status = "rejected";
      return route.fulfill({ json: { ok: true, status: "replan_requested", candidate_preview: "Keep the race duration at 120 seconds." } });
    }
    expect(payload.plan_id).toBe(plan.plan_id);
    if (payload.operation === "discuss") {
      expect(payload.mode).toBe("chat");
      expect(payload.quote.text).toBe("Keep the race duration at 90 seconds.");
      expect(payload.quote.start).toBe(plan.markdown.indexOf("Keep the race duration"));
    }
    return route.fulfill({ json: payload.operation === "context"
      ? { ok: true, body: plan.markdown, digest: "fixture-digest", editable: plan.status === "pending" }
      : { ok: true, answer: "Review this candidate.", before: "90 seconds", after: "120 seconds", preview: "# Racing game plan\n\nKeep the race duration at 120 seconds.", draft_ref: { ref: "fixture-candidate" } } });
  });
  await page.route("**/actions/plan-approve", route => {
    const body = route.request().postDataJSON();
    expect(body.payload.plan_id).toBe(plan.plan_id);
    expect(body.payload.binding_digest).toBe(plan.action_payload.binding_digest);
    approvals++;
    plan.status = "approved";
    return route.fulfill({ json: { ok: true, status: "accepted", action: "plan-approve" } });
  });
  await page.goto(`/?page=workflows&project=default&request=${id}`);
  await expect(page.getByLabel("Select workflow")).toHaveValue(id);
  await page.getByLabel("Select workflow").selectOption(other);
  await expect(page).toHaveURL(new RegExp(`request=${other}`));
  await expect(page.getByRole("button", { name: "Review", exact: true })).toHaveCount(0);
  await page.getByLabel("Select workflow").selectOption(id);
  await page.getByRole("button", { name: "Review", exact: true }).click();
  const preview = page.getByRole("region", { name: "Workflow PRD Plan Preview" });
  await expect(preview).toContainText("Keep the race duration at 90 seconds.");
  await preview.getByRole("button", { name: "Raw", exact: true }).click();
  const source = preview.getByTestId("plan-review-source");
  await expect(source).toHaveText(plan.markdown);
  await source.evaluate(element => {
    const text = element.firstChild!;
    const start = text.textContent!.indexOf("Keep the race duration");
    const range = document.createRange(); range.setStart(text, start); range.setEnd(text, text.textContent!.length);
    const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range);
    element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });
  await preview.getByRole("button", { name: "引用到 Agent", exact: true }).click();
  const agent = preview.getByRole("dialog", { name: "Plan Agent", exact: true });
  await expect(agent).toBeVisible();
  await expect(agent.getByRole("textbox")).toHaveCount(1);
  expect((await agent.boundingBox())!.width).toBeLessThanOrEqual(440);
  expect((await agent.boundingBox())!.height).toBeLessThan(320);
  await expect(page.locator("dialog[open]")).toHaveCount(0);
  await expect(agent.getByTestId("plan-review-quote")).toContainText("90 seconds");
  await agent.getByLabel("Plan artifact discussion").fill("Extend the duration.");
  await agent.getByRole("textbox").press("Control+Enter");
  await expect(preview.getByTestId("plan-review-diff")).toContainText("120 seconds");
  await expect(preview.getByRole("button", { name: "确认计划并继续", exact: true })).toBeDisabled();
  expect(approvals).toBe(0);
  await agent.getByRole("button", { name: "Close agent chat" }).click();
  await preview.getByRole("button", { name: "Agent", exact: true }).click();
  await expect(agent.getByTestId("plan-review-diff")).toContainText("120 seconds");
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow(page);
  await agent.getByRole("button", { name: "Discard", exact: true }).click();
  await expect(preview.getByRole("button", { name: "确认计划并继续", exact: true })).toBeEnabled();
  await agent.getByRole("textbox").fill("Apply the duration change for a new plan review.");
  await agent.getByRole("button", { name: "Send", exact: true }).click();
  await agent.getByRole("button", { name: "Apply", exact: true }).click();
  expect(revisions).toBe(1);
  expect(approvals).toBe(0);
  await expect(preview.getByRole("button", { name: "确认计划并继续", exact: true })).toHaveCount(0);
  await expect(agent.getByTestId("plan-agent-availability")).toContainText("等待新版 Plan Preview");
  plan.plan_id = "plan-ui-revised";
  plan.status = "pending";
  plan.markdown = "# Racing game plan\n\nKeep the race duration at 120 seconds.";
  plan.action_payload.binding_digest = "fixture-plan-version-2";
  // This fixture mutates its response without a Planner event.
  await expect(preview).toHaveCount(0, { timeout: 35_000 });
  await page.getByRole("button", { name: "Review", exact: true }).click();
  await expect(preview).toContainText("Keep the race duration at 120 seconds.");
  await preview.getByRole("button", { name: "确认计划并继续", exact: true }).click();
  await expect(preview).toHaveCount(0);
  expect(approvals).toBe(1);
});

test("Plan main reader quotes formatted blocks, edits repeated paragraphs and selects exact Raw text", async ({ page, request }) => {
  const id = `plan-entry-${Date.now()}`;
  await prepare(request, id);
  const graph = await (await request.get(`/api/projects/default/workflow-requests/${id}/graph`)).json();
  const paragraph = "玩家😀点击 **加一**，数字增加 `1`。";
  const body = `# 计划😀\n\n${paragraph}\n\n${paragraph}\n`;
  const plan = { plan_id: "plan-entry-fixture", status: "pending", markdown: body,
    artifacts: [{ name: "accepted_plan", ref: "fixtures/plan.md", sha256: "bound-fixture" }] };
  let expectedQuote = { text: paragraph, start: Array.from(body.slice(0, body.indexOf(paragraph))).length, end: 0 };
  expectedQuote.end = expectedQuote.start + Array.from(paragraph).length;
  let requests = 0;
  let failContext = true;
  await page.route(`**/api/projects/*/workflow-requests/${id}/graph*`, route => route.fulfill({ json: { ...graph, plans: [plan] } }));
  await page.route("**/plan-artifact-review", route => {
    const payload = route.request().postDataJSON();
    if (payload.operation === "context") return route.fulfill({ status: failContext ? 503 : 200,
      json: failContext ? { ok: false, reason: "fixture context unavailable" } : { ok: true, body, digest: "bound-fixture", editable: plan.status === "pending" } });
    expect(payload.operation).toBe("discuss");
    expect(payload.digest).toBe("bound-fixture");
    expect(payload.quote).toMatchObject(expectedQuote);
    requests++;
    return route.fulfill({ json: { ok: true, answer: "引用范围已确认。", draft_ref: null } });
  });
  await page.goto(`/?page=workflows&project=default&request=${id}`);
  await page.getByRole("button", { name: "Review", exact: true }).click();
  const panel = page.getByRole("region", { name: "Workflow PRD Plan Preview" });
  await expect(panel.getByRole("alert")).toContainText("文档加载失败");
  failContext = false;
  await panel.getByRole("button", { name: "重试", exact: true }).click();
  const blocks = panel.locator(".plan-review-block");
  await expect(blocks).toHaveCount(3);
  expect(await panel.boundingBox()).toMatchObject({ x: 0, y: 0, ...page.viewportSize()! });
  expect((await panel.getByTestId("plan-review-document").boundingBox())!.height).toBeGreaterThan(page.viewportSize()!.height - 230);
  await expect(blocks.first().getByRole("heading")).toHaveCSS("font-size", "20px");
  await expect(blocks.nth(1).locator('[data-streamdown="strong"]')).toHaveText("加一");
  await expect(blocks.nth(1).locator('[data-streamdown="strong"]')).toHaveCSS("font-weight", "700");
  await expect(panel.getByRole("button", { name: "引用编辑此段" })).toHaveCount(0);
  const quoteAction = panel.getByRole("button", { name: "引用到 Agent", exact: true });
  await expect(quoteAction).toHaveCount(0);
  await expect(panel.getByTestId("plan-review-source")).toHaveCount(0);
  // Selection in the primary formatted reading surface, including Markdown emphasis.
  await expect(blocks.nth(1).locator("code")).toHaveText("1");
  await selectText(blocks.nth(1).locator("p"));
  await expect(quoteAction).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(quoteAction).toHaveCount(0);
  await selectText(blocks.nth(1).locator("p"));
  await expect(quoteAction).toBeVisible();
  await panel.getByTestId("plan-review-document").evaluate(element => element.dispatchEvent(new Event("scroll")));
  await expect(quoteAction).toHaveCount(0);
  await selectText(blocks.nth(1).locator("p"));
  await quoteAction.click();
  const agent = panel.getByRole("dialog", { name: "Plan Agent", exact: true });
  await expect(agent).toBeVisible();
  const toolbar = panel.getByRole("navigation", { name: "Plan document view" });
  await expect(toolbar.locator(":scope > button")).toHaveText(["Preview", "Raw", "Agent"]);
  expect((await agent.boundingBox())!.y - (await toolbar.boundingBox())!.y - (await toolbar.boundingBox())!.height).toBeLessThan(16);
  await expect(agent.getByTestId("plan-review-quote")).toContainText(paragraph);
  await agent.getByRole("textbox").fill("讨论这段验收条件");
  await agent.getByRole("button", { name: "Send", exact: true }).click();
  await expect(agent).toContainText("引用范围已确认。");
  // A second identical paragraph must bind its own position, including non-BMP characters.
  expectedQuote.start = Array.from(body.slice(0, body.lastIndexOf(paragraph))).length;
  expectedQuote.end = expectedQuote.start + Array.from(paragraph).length;
  await selectText(blocks.nth(2).locator("p"));
  // A new native selection alone must not replace the quote already in chat.
  await expect(agent.getByTestId("plan-review-quote")).toContainText(paragraph);
  await quoteAction.click();
  await expect(agent).toBeVisible();
  await agent.getByRole("textbox").fill("只讨论第二段");
  await agent.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(() => requests).toBe(2);
  await agent.getByRole("button", { name: "Close agent chat" }).click();
  await panel.getByRole("button", { name: "Raw", exact: true }).click();
  const source = panel.getByTestId("plan-review-source");
  const rawStart = body.lastIndexOf("加一");
  expectedQuote = { text: "加一", start: Array.from(body.slice(0, rawStart)).length, end: Array.from(body.slice(0, rawStart)).length + 2 };
  await source.evaluate((element, start) => {
    const range = document.createRange(); range.setStart(element.firstChild!, start); range.setEnd(element.firstChild!, start + 2);
    const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range);
  }, rawStart);
  await quoteAction.click();
  await expect(agent.getByTestId("plan-review-quote")).toHaveText("加一");
  await agent.getByRole("textbox").fill("讨论这两个字");
  await agent.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(() => requests).toBe(3);
  await agent.getByRole("button", { name: "Close agent chat" }).click();
  await panel.getByRole("button", { name: "Preview", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await panel.boundingBox()).toMatchObject({ x: 0, y: 0, width: 390, height: 844 });
  expect((await panel.getByTestId("plan-review-document").boundingBox())!.height).toBeGreaterThan(564);
  await selectText(blocks.nth(1).locator("p"));
  await quoteAction.click();
  await expect(agent).toBeVisible();
  await noOverflow(page);
  expect((await agent.boundingBox())!.width).toBeLessThan(390);
  await panel.screenshot({ path: test.info().outputPath("plan-entry-mobile.png") });
  plan.status = "approved";
  // No approval event is emitted by this mocked response.
  await expect(agent.getByRole("textbox")).toBeDisabled({ timeout: 35_000 });
  await selectText(blocks.nth(1).locator("p"));
  await expect(quoteAction).toHaveCount(0);
  expect(requests).toBe(3);
});
