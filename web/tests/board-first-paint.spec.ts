import { expect, test, type Page } from "@playwright/test";

function snapshot(project: string, title = `Task ${project}`) {
  return { seq: 10, generated_at: "2026-10-26T00:00:00Z", project: { project_id: project, name: project, root: "/fixture", state_dir: "/fixture/state" },
    tasks: [{ id: `TASK-${project}`, title, status: "in_progress", assigned_to: "dev-1", retry_count: 0, priority: 2, kanban_column: "in_progress" }],
    archive_tasks: [], features: [], delivery_features: [], traces: [], fanouts: [], channels: [], agents: [], roles: [], workdirs: [], workers: [], runs: [], active_runs: [],
    cost: { total_usd: 0.00000123, input_tokens: 123456789, output_tokens: 3 }, skills: {},
    runtime: { live: false, web_session: { mode: "read_only" }, actions: { mutation_enabled: false, allowed: [] }, sessions: {}, workdirs: {} } };
}
const projects = ["a", "b"].map(project_id => ({ project_id, name: project_id, root: "/fixture", can_open_board: true, lifecycle: { initialized: true, can_open_board: true } }));

async function mockApi(page: Page) {
  await page.route(/^http:\/\/[^/]+\/api\//, async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/workspace/projects") return route.fulfill({ json: { items: projects, active_project_id: "a" } });
    if (path === "/api/workspace/onboarding") return route.fulfill({ json: { completed: true, skipped: true, backends: [] } });
    if (path === "/api/web-session") return route.fulfill({ json: { mode: "read_only" } });
    if (path.endsWith("/stream")) return route.fulfill({ contentType: "text/event-stream", body: ": fixture\n\n" });
    if (path.endsWith("/health/summary")) return route.fulfill({ json: { seq: 10, runtime_state: "stopped", active: 1, blocked: 0, projection: { state: "ready" } } });
    if (path.endsWith("/snapshot/board")) return route.fulfill({ json: snapshot(path.split("/")[3]) });
    if (path.endsWith("/snapshot/light")) return route.fulfill({ json: snapshot(path.split("/")[3]) });
    if (path.endsWith("/overview-pulse")) return route.fulfill({ json: { schema_version: "overview-pulse.v1", run_pulse: { events_per_bucket: [], loop: {}, sessions: {} }, task_flow: null, attention: null, why_not: null } });
    return route.fulfill({ json: { ok: true, items: [], summary: { task_count: 0, total: 0 } } });
  });
}

test("Board displays before workspace and health, and late health cannot restore an old snapshot", async ({ page }) => {
  await mockApi(page);
  let releaseWorkspace!: () => void;
  let releaseHealth!: () => void;
  const workspace = new Promise<void>(resolve => { releaseWorkspace = resolve; });
  const health = new Promise<void>(resolve => { releaseHealth = resolve; });
  let workspaceStarted = false;
  let revision = 1;
  const snapshotPaths: string[] = [];
  page.on("request", req => { if (req.url().includes("/snapshot/")) snapshotPaths.push(new URL(req.url()).pathname); });
  await page.route("**/api/workspace/projects", async route => {
    workspaceStarted = true;
    await workspace;
    await route.fulfill({ json: { items: projects, active_project_id: "a" } }).catch(() => {});
  });
  await page.route("**/health/summary", async route => {
    await health;
    await route.fulfill({ json: { seq: 10, active: 1, blocked: 0, runtime_state: "stopped", projection: { state: "ready" } } }).catch(() => {});
  });
  await page.route("**/snapshot/board", route => route.fulfill({ json: snapshot("a", `Revision ${revision}`) }));
  try {
    await page.goto("/?project=a&page=board");
    await expect(page.locator(".board-grid")).toContainText("Revision 1");
    await expect.poll(() => workspaceStarted).toBe(true);
    await expect(page.getByText("Loading board…", { exact: true })).toHaveCount(0);
    releaseWorkspace();
    revision = 2;
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(page.locator(".board-grid")).toContainText("Revision 2");
    releaseHealth();
    await expect(page.locator(".topbar-activity")).toContainText("1 active");
    await expect(page.locator(".board-grid")).toContainText("Revision 2");
    expect(snapshotPaths.every(path => path.endsWith("/snapshot/board"))).toBe(true);
  } finally { releaseWorkspace(); releaseHealth(); }
});

test("project switch and delayed Board refresh keep the current project's task", async ({ page }) => {
  await mockApi(page);
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let hold = false;
  let seen = false;
  await page.route("**/projects/a/snapshot/board", async route => {
    if (hold) { seen = true; await held; }
    await route.fulfill({ json: snapshot("a") }).catch(() => {});
  });
  try {
    await page.goto("/?project=a&page=board");
    await expect(page.locator(".board-grid")).toContainText("Task a");
    await expect(page.getByLabel("Project", { exact: true })).toHaveValue("a");
    hold = true;
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect.poll(() => seen).toBe(true);
    await page.getByLabel("Project", { exact: true }).selectOption("b");
    await page.getByRole("button", { name: "Tasks", exact: true }).click();
    await expect(page.locator(".board-grid")).toContainText("Task b");
    release();
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await expect(page.locator(".board-grid")).not.toContainText("Task a");
  } finally { release(); }
});

test("Board resolves a removed persisted project before requesting its snapshot", async ({ page }) => {
  await mockApi(page);
  await page.addInitScript(() => localStorage.setItem("zf.activeProjectId", "removed"));
  const snapshots: string[] = [];
  page.on("request", req => { if (req.url().includes("/snapshot/")) snapshots.push(req.url()); });
  await page.goto("/?page=board");
  await expect(page.locator(".board-grid")).toContainText("Task a");
  await expect(page.getByLabel("Project", { exact: true })).toHaveValue("a");
  expect(snapshots.length).toBeGreaterThan(0);
  expect(snapshots.every(url => url.includes("/projects/a/snapshot/board"))).toBe(true);
});

test("Overview keeps its summary cards while using the body-free task snapshot", async ({ page }) => {
  await mockApi(page);
  const paths: string[] = [];
  page.on("request", req => { if (req.url().includes("/snapshot/")) paths.push(new URL(req.url()).pathname); });
  await page.goto("/?project=a&page=project");
  await expect(page.locator(".project-health-card")).toHaveCount(5);
  await expect(page.locator(".project-health-card").filter({ hasText: "Work Queue" }).locator("strong")).toHaveText("1");
  expect(paths.length).toBeGreaterThan(0);
  expect(paths.every(path => path.endsWith("/snapshot/board"))).toBe(true);
});

test("stream gap reloads the Board summary and reconnects at the recovered cursor", async ({ page }) => {
  await mockApi(page);
  let recovered = false;
  const streamUrls: string[] = [];
  const snapshots: string[] = [];
  page.on("request", req => { if (req.url().includes("/snapshot/")) snapshots.push(req.url()); });
  await page.route("**/projects/a/events?*", route => {
    recovered = true;
    return route.fulfill({ json: { items: [], current_seq: 42 } });
  });
  await page.route("**/projects/a/snapshot/board", route => route.fulfill({ json: snapshot("a", recovered ? "Recovered task" : "Initial task") }));
  await page.route("**/projects/a/stream*", route => {
    streamUrls.push(route.request().url());
    return route.fulfill({ contentType: "text/event-stream", body: streamUrls.length === 1
      ? 'event: stream.gap\ndata: {"type":"stream.gap","seq":11}\n\n'
      : ": fixture\n\n" });
  });
  await page.goto("/?project=a&page=board");
  await expect(page.locator(".board-grid")).toContainText("Recovered task");
  await expect.poll(() => streamUrls.some(url => new URL(url).searchParams.get("cursor") === "42")).toBe(true);
  expect(snapshots.every(url => url.includes("/snapshot/board"))).toBe(true);
});

test("Observability keeps its metrics and refreshes through its scoped snapshot", async ({ page }) => {
  await mockApi(page);
  const paths: string[] = [];
  const errors: string[] = [];
  const panelPaths: string[] = [];
  page.on("request", request => { if (/\/(integration-queue|repair-actions|task-pipeline)(\?|$)/.test(request.url())) panelPaths.push(new URL(request.url()).pathname); });
  let revision = 1;
  page.on("pageerror", error => errors.push(String(error)));
  page.on("request", request => { if (request.url().includes("/snapshot")) paths.push(new URL(request.url()).pathname); });
  await page.route("**/snapshot/observability", route => route.fulfill({ json: {
    ...snapshot("a"), tasks: [], archive_tasks: [],
    traces: Array.from({ length: revision }, (_, index) => ({ trace_id: `trace-${index}`, event_count: 42, first_seq: 1, last_seq: 42, task_ids: [], actors: [] })),
    runs: [{ run_id: "run1" }], active_runs: [],
    fanouts: [{ fanout_id: "fanout1" }],
    agents: [{ instance_id: "dev-1", context_usage_ratio: 0.25 }],
  } }));
  await page.goto("/?project=a&page=events");
  const traceCount = page.locator(".projection-summary-card").filter({ hasText: "Traces" }).locator("strong");
  await expect(traceCount).toHaveText("1");
  await expect(page.locator(".projection-summary-grid")).toContainText("25%");
  revision = 2;
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(traceCount).toHaveText("2");
  await expect(page.locator(".projection-summary-card")).toHaveCount(6);
  expect(paths.length).toBeGreaterThanOrEqual(2);
  expect(paths.every(path => path.endsWith("/snapshot/observability"))).toBe(true);
  expect(panelPaths).toEqual([]);
  await page.getByRole("button", { name: /^Integration/ }).click();
  await expect.poll(() => panelPaths.some(path => path.endsWith("/integration-queue"))).toBe(true);
  expect(panelPaths.every(path => path.endsWith("/integration-queue"))).toBe(true);
  expect(errors).toEqual([]);
});

test("first SQLite generation stays loading until ready, without a fake empty Board", async ({ page }) => {
  await mockApi(page);
  let ready = false;
  await page.route("**/projects/a/snapshot/board", route => route.fulfill(ready
    ? { json: { ...snapshot("a"), read_projection: { state: "ready", generated_at_epoch: 1_790_000_000 } } }
    : { status: 202, json: { read_projection: { state: "building" } } }));
  await page.goto("/?project=a&page=board");
  await expect(page.getByText("Loading board…", { exact: true })).toBeVisible();
  await expect(page.locator(".board-grid .task-card")).toHaveCount(0);
  ready = true;
  await expect(page.locator(".board-grid")).toContainText("Task a");
});

test("stale SQLite generation stays visible with a notice and updates without an event seq change", async ({ page }) => {
  await mockApi(page);
  let ready = false;
  await page.route("**/projects/a/snapshot/board", route => route.fulfill({ json: {
    ...snapshot("a", ready ? "Updated projection" : "Previous projection"),
    read_projection: { state: ready ? "ready" : "refreshing", generated_at_epoch: 1_790_000_000 },
  } }));
  await page.goto("/?project=a&page=board");
  await expect(page.locator(".board-grid")).toContainText("Previous projection");
  await expect(page.getByRole("status")).toContainText("当前显示上次结果");
  ready = true;
  await expect(page.locator(".board-grid")).toContainText("Updated projection");
  await expect(page.getByText("正在更新数据", { exact: false })).toHaveCount(0);
});

test("ready projection refreshes clock-derived values without new events", async ({ page }) => {
  await page.clock.install();
  await mockApi(page);
  let changed = false;
  await page.route("**/projects/a/snapshot/board", route => route.fulfill({ json: {
    ...snapshot("a", changed ? "Clock refreshed" : "Initial clock"),
    read_projection: { state: "ready", generated_at_epoch: 1_790_000_000 },
  } }));
  await page.goto("/?project=a&page=board");
  await expect(page.locator(".board-grid")).toContainText("Initial clock");
  changed = true;
  await page.clock.fastForward(31_000);
  await expect(page.locator(".board-grid")).toContainText("Clock refreshed");
});
