import { expect, test } from "@playwright/test";

const projectId = process.env.ZF_WEB_PROJECT_ID ?? "zaofu-915bc1fe";

// These fixtures pin historical cursors; real project SSE must not advance them.
// Live invalidation is exercised separately in board-first-paint.spec.ts.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    (window as any).EventSource = class {
      onmessage = null; onerror = null; onopen: (() => void) | null = null;
      constructor() { setTimeout(() => this.onopen?.(), 0); }
      addEventListener() {} close() {}
    };
  });
});

test("traces load a bounded index and disclose detail, history, and raw data on demand", async ({ page }) => {
  const traceListRequests: string[] = [];
  const traceDetailRequests: string[] = [];
  const traceSpanRequests: string[] = [];
  const rawEventRequests: string[] = [];
  const forbiddenBootstrapRequests: string[] = [];
  let workspaceRequestObserved = false;
  let releaseWorkspace!: () => void;
  const workspaceGate = new Promise<void>((resolve) => {
    releaseWorkspace = resolve;
  });

  await page.route("**/api/workspace/projects", async (route) => {
    workspaceRequestObserved = true;
    await workspaceGate;
    await route.continue();
  });

  page.on("request", (request) => {
    const url = new URL(request.url());
    if (/\/(snapshot(?:\/light)?|events|cost|integration-queue|repair-actions|task-pipeline)$/.test(url.pathname)) {
      forbiddenBootstrapRequests.push(url.pathname);
    }
  });

  await page.route(/\/api\/projects\/[^/]+\/traces\?.*/, async (route) => {
    traceListRequests.push(route.request().url());
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "trace-list.v2",
        items: [{
          trace_id: "trace-demo",
          first_seq: 1,
          last_seq: 2,
          first_ts: "2026-08-19T08:00:00Z",
          last_ts: "2026-08-19T08:02:00Z",
          duration_seconds: 120,
          event_count: 2,
          task_ids: ["TASK-DEMO"],
          actors: ["developer"],
          backends: ["codex-headless"],
          status: "completed",
          last_type: "task.done",
          source: "event_trace",
        }, {
          trace_id: "trace-empty",
          first_seq: 3,
          last_seq: 4,
          first_ts: "2026-08-19T09:00:00Z",
          last_ts: "2026-08-19T09:01:00Z",
          duration_seconds: 60,
          event_count: 2,
          task_ids: ["TASK-DEMO"],
          actors: ["tester"],
          backends: ["codex-headless"],
          status: "completed",
          last_type: "test.passed",
          source: "event_trace",
        }],
        limit: 50,
        has_more: false,
        next_cursor: null,
        as_of_seq: 2,
        is_derived_projection: true,
      }),
    });
  });

  await page.route(/\/api\/projects\/[^/]+\/traces\/trace-demo\/spans\?.*/, async (route) => {
    traceSpanRequests.push(route.request().url());
    const params = new URL(route.request().url()).searchParams;
    const oldSpan = {
      trace_id: "trace-demo",
      span_id: "span-old",
      parent_span_id: null,
      name: "Older lifecycle span",
      kind: "agent.session.run",
      status: "completed",
      started_at: "2026-08-19T07:00:00Z",
      ended_at: "2026-08-19T07:01:00Z",
      duration_seconds: 60,
      source: "events.jsonl",
      truth_class: "paired_lifecycle",
      degraded: false,
      degradation_reason: null,
      source_event_ids: ["evt-old-start", "evt-old-done"],
    };
    const recentSpans = [{
      trace_id: "trace-demo",
      span_id: "span-root",
      parent_span_id: null,
      name: "Root attempt",
      kind: "task.attempt",
      status: "completed",
      started_at: "2026-08-19T08:00:00Z",
      ended_at: "2026-08-19T08:02:00Z",
      duration_seconds: 120,
      source: "events.jsonl",
      truth_class: "kernel.lifecycle",
      degraded: false,
      degradation_reason: null,
      source_event_ids: ["evt-span-start", "evt-2"],
      task_id: "TASK-DEMO",
      actor: "developer",
      backend: "codex-headless",
      provenance: { pairing: "stable attempt id" },
    }, {
      trace_id: "trace-demo",
      span_id: "span-child",
      parent_span_id: "span-root",
      name: "Recovered child",
      kind: "runtime.action.attempt",
      status: "observed",
      started_at: "2026-08-19T08:01:00Z",
      ended_at: null,
      duration_seconds: null,
      source: "events.jsonl",
      truth_class: "kernel.lifecycle.degraded",
      degraded: true,
      degradation_reason: "terminal boundary unavailable",
      source_event_ids: ["evt-child-start"],
    }];
    const cursor = params.get("cursor");
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "trace-spans.v1",
        trace_id: "trace-demo",
        items: cursor ? [oldSpan] : recentSpans,
        focused_item: params.get("focus_span_id") === "span-old" ? oldSpan : null,
        span_count: 101,
        limit: 100,
        has_more: !cursor,
        next_cursor: cursor ? null : "older-spans-1",
        as_of_seq: 2,
        coverage: {
          status: "partial",
          reason: "provider-native spans are unobserved",
          collector: "unobserved",
          ledger: "events.jsonl",
          eligible_event_count: 3,
          paired_span_count: 1,
          degraded_span_count: 1,
        },
      }),
    });
  });

  await page.route(/\/api\/projects\/[^/]+\/traces\/trace-empty\/spans\?.*/, async (route) => {
    traceSpanRequests.push(route.request().url());
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "trace-spans.v1",
        trace_id: "trace-empty",
        items: [],
        span_count: 0,
        limit: 100,
        has_more: false,
        next_cursor: null,
        as_of_seq: 4,
        coverage: {
          status: "empty",
          reason: "No paired lifecycle boundaries were found in the canonical trace ledger.",
          collector: "unobserved",
          ledger: "events.jsonl",
          eligible_event_count: 0,
          paired_span_count: 0,
          degraded_span_count: 0,
        },
      }),
    });
  });

  await page.route(/\/api\/projects\/[^/]+\/traces\/trace-demo\?.*/, async (route) => {
    traceDetailRequests.push(route.request().url());
    const cursor = new URL(route.request().url()).searchParams.get("cursor");
    const timeline = cursor ? [{
      seq: 1,
      id: "evt-1",
      ts: "2026-08-19T08:00:00Z",
      type: "task.started",
      actor: "developer",
      task_id: "TASK-DEMO",
      status: "running",
      summary: "Task started",
      has_raw: true,
      payload_slim: true,
    }] : [{
      seq: 2,
      id: "evt-2",
      ts: "2026-08-19T08:02:00Z",
      type: "task.done",
      actor: "developer",
      task_id: "TASK-DEMO",
      status: "completed",
      summary: "Task completed",
      has_raw: true,
      payload_slim: true,
    }];
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "trace-detail.v2",
        trace_id: "trace-demo",
        event_count: 2,
        first_seq: 1,
        last_seq: 2,
        first_ts: "2026-08-19T08:00:00Z",
        last_ts: "2026-08-19T08:02:00Z",
        duration_seconds: 120,
        status: "completed",
        tasks: ["TASK-DEMO"],
        actors: ["developer"],
        timeline,
        truncated: !cursor,
        has_more: !cursor,
        next_cursor: cursor ? null : "older-1",
        as_of_seq: 2,
        empty: false,
      }),
    });
  });

  await page.route(/\/api\/projects\/[^/]+\/traces\/trace-empty\?.*/, async (route) => {
    traceDetailRequests.push(route.request().url());
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "trace-detail.v2",
        trace_id: "trace-empty",
        event_count: 2,
        first_seq: 3,
        last_seq: 4,
        first_ts: "2026-08-19T09:00:00Z",
        last_ts: "2026-08-19T09:01:00Z",
        duration_seconds: 60,
        status: "completed",
        tasks: ["TASK-DEMO"],
        actors: ["tester"],
        timeline: [],
        execution_route: {
          schema_version: "execution-route-summary.v2",
          scope: { task_id: "TASK-DEMO", trace_id: "trace-empty" },
          status: "done",
          current_stage: "test",
          current_stage_label: "Test",
          summary: "Test done",
          step_count: 1,
          parallel: false,
          linear: [{
            stage: "test",
            label: "Test",
            status: "done",
            parallel: false,
            actors: ["tester"],
            first_seq: 3,
            last_seq: 4,
            first_ts: "2026-08-19T09:00:00Z",
            last_ts: "2026-08-19T09:01:00Z",
            event_count: 2,
            event_types: ["test.started", "test.passed"],
            task_ids: ["TASK-DEMO"],
            failed_count: 0,
            values_truncated: false,
          }],
          trace_event_count: 2,
          source_event_count: 2,
          metadata_truncated: false,
          empty: false,
        },
        truncated: false,
        has_more: false,
        next_cursor: null,
        as_of_seq: 4,
        empty: false,
      }),
    });
  });

  await page.route(/\/api\/projects\/[^/]+\/events\/(evt-2|evt-span-start|evt-child-start)$/, async (route) => {
    rawEventRequests.push(route.request().url());
    const eventId = new URL(route.request().url()).pathname.split("/").at(-1) ?? "";
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "event-detail.v1",
        event_id: eventId,
        event: {
          seq: 2,
          id: eventId,
          ts: "2026-08-19T08:02:00Z",
          type: "task.done",
          actor: "developer",
          task_id: "TASK-DEMO",
          payload: { result: `verified raw payload for ${eventId}` },
        },
        source: "events.jsonl",
      }),
    });
  });

  await page.goto(
    `/?project=${encodeURIComponent(projectId)}&page=observability&obs_tab=traces`,
  );
  await expect(page.getByTestId("traces-page")).toBeVisible();
  await expect(page.getByRole("table", { name: "Trace index" })).toBeVisible();
  await expect(page.getByRole("button", { name: "trace-demo" })).toBeVisible();
  await expect(page).toHaveURL(/page=traces/);
  expect(new URL(page.url()).searchParams.has("obs_tab")).toBe(false);
  expect(workspaceRequestObserved).toBe(true);
  expect(traceListRequests).toHaveLength(1);
  expect(new URL(traceListRequests[0]).searchParams.get("contract")).toBe("v2");
  expect(new URL(traceListRequests[0]).searchParams.get("limit")).toBe("50");
  expect(traceDetailRequests).toHaveLength(0);
  expect(forbiddenBootstrapRequests).toEqual([]);
  releaseWorkspace();

  await page.getByRole("searchbox", { name: "Search traces" }).fill("TASK-DEMO");
  await page.getByRole("button", { name: "trace-demo" }).click();
  await expect(page.getByRole("region", { name: "Trace trace-demo" })).toBeVisible();
  await expect(page).toHaveURL(/trace_id=trace-demo/);
  await expect(page).toHaveURL(/span_id=span-root/);
  await expect(page.getByRole("tab", { name: /^Spans/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tree", { name: "Span tree" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Root attempt/ })).toHaveAttribute("aria-current", "true");
  await expect(page.getByRole("complementary", { name: "Trace inspector" }).getByText("kernel.lifecycle", { exact: true })).toBeVisible();
  const desktopViewer = await page.getByRole("region", { name: "Trace trace-demo" }).boundingBox();
  expect(desktopViewer?.width ?? 0).toBeGreaterThan(680);
  expect(traceDetailRequests).toHaveLength(1);
  expect(new URL(traceDetailRequests[0]).searchParams.get("limit")).toBe("80");
  expect(traceSpanRequests).toHaveLength(1);
  expect(new URL(traceSpanRequests[0]).searchParams.get("contract")).toBe("v1");
  expect(new URL(traceSpanRequests[0]).searchParams.get("limit")).toBe("100");
  expect(rawEventRequests).toHaveLength(0);
  await page.getByRole("button", { name: "Load earlier spans" }).click();
  await expect(page.getByRole("button", { name: /Older lifecycle span/ })).toBeVisible();
  expect(traceSpanRequests).toHaveLength(2);
  expect(new URL(traceSpanRequests[1]).searchParams.get("cursor")).toBe("older-spans-1");

  await page.getByRole("tab", { name: "Waterfall" }).click();
  await expect(page.getByRole("group", { name: "Span waterfall" })).toBeVisible();
  await expect(page.getByText("Timing unavailable", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Recovered child/ }).click();
  await expect(page).toHaveURL(/span_id=span-child/);
  await expect(page.getByText(/terminal boundary unavailable/)).toBeVisible();
  expect(rawEventRequests).toHaveLength(0);
  await page.getByRole("tab", { name: "Raw" }).click();
  await expect(page.getByText(/verified raw payload for evt-child-start/)).toBeVisible();
  expect(rawEventRequests).toHaveLength(1);

  await page.getByRole("tab", { name: /^Execution/ }).click();
  await expect(page.getByText("No structured route is available for this trace.")).toBeVisible();
  await page.getByRole("tab", { name: /^Events/ }).click();
  await expect(page.getByText("Task completed", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Load earlier" }).click();
  await expect(page.getByText("Task started", { exact: true })).toBeVisible();
  expect(traceDetailRequests).toHaveLength(2);
  expect(new URL(traceDetailRequests[1]).searchParams.get("cursor")).toBe("older-1");

  await page.getByRole("button", { name: /Task completed/ }).click();
  await page.getByRole("tab", { name: "Raw" }).click();
  await expect(page.getByText(/verified raw payload for evt-2/)).toBeVisible();
  expect(rawEventRequests).toHaveLength(2);

  await page.getByRole("button", { name: "Back to traces" }).click();
  await expect(page.getByRole("region", { name: "Trace trace-demo" })).toHaveCount(0);
  await expect(page.getByRole("searchbox", { name: "Search traces" })).toHaveValue("TASK-DEMO");
  await expect(page).not.toHaveURL(/trace_id=/);
  await expect(page).not.toHaveURL(/span_id=/);

  await page.goto(`/?project=${encodeURIComponent(projectId)}&page=traces&trace_id=trace-demo&span_id=span-old`);
  await expect(page.getByRole("region", { name: "Trace trace-demo" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Older lifecycle span/ })).toHaveAttribute("aria-current", "true");
  await expect(page).toHaveURL(/span_id=span-old/);
  expect(new URL(traceSpanRequests[2]).searchParams.get("focus_span_id")).toBe("span-old");
  expect(rawEventRequests).toHaveLength(2);
  await page.getByRole("button", { name: "Back to traces" }).click();

  await page.getByRole("button", { name: "trace-empty" }).click();
  await expect(page.getByRole("tab", { name: /^Execution/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("button", { name: /Test.*2 events/ })).toBeVisible();
  await page.getByRole("tab", { name: /^Spans/ }).click();
  await expect(page.getByText("No lifecycle spans are proven for this trace.")).toBeVisible();
  await expect(page.getByText(/No paired lifecycle boundaries/)).toBeVisible();
  await expect(page.getByText("unobserved", { exact: true })).toBeVisible();
  expect(traceSpanRequests).toHaveLength(4);
  expect(traceDetailRequests).toHaveLength(4);
  await page.getByRole("button", { name: "Open Execution" }).click();
  await expect(page.getByRole("tab", { name: /^Execution/ })).toHaveAttribute("aria-selected", "true");

  await page.setViewportSize({ width: 820, height: 760 });
  const tabletViewer = await page.getByRole("region", { name: "Trace trace-empty" }).boundingBox();
  const tabletPrimary = await page.locator(".trace-workspace-primary").boundingBox();
  const tabletInspector = await page.getByRole("complementary", { name: "Trace inspector" }).boundingBox();
  expect(Math.round(tabletViewer?.width ?? 0)).toBe(820);
  expect((tabletInspector?.x ?? 0) > (tabletPrimary?.x ?? 0)).toBe(true);

  await page.setViewportSize({ width: 600, height: 760 });
  const mobileViewer = await page.getByRole("region", { name: "Trace trace-empty" }).boundingBox();
  const mobilePrimary = await page.locator(".trace-workspace-primary").boundingBox();
  const mobileInspector = await page.getByRole("complementary", { name: "Trace inspector" }).boundingBox();
  expect(Math.round(mobileViewer?.width ?? 0)).toBe(600);
  expect((mobileInspector?.y ?? 0) > (mobilePrimary?.y ?? 0)).toBe(true);
  await page.getByRole("button", { name: "Back to traces" }).click();
});

test("an unscoped trace route waits for workspace project resolution", async ({ page }) => {
  const traceRequests: string[] = [];
  let releaseWorkspace!: () => void;
  const workspaceGate = new Promise<void>((resolve) => {
    releaseWorkspace = resolve;
  });

  await page.route("**/api/workspace/projects", async (route) => {
    await workspaceGate;
    await route.continue();
  });
  page.on("request", (request) => {
    if (/\/api\/projects\/[^/]+\/traces$/.test(new URL(request.url()).pathname)) {
      traceRequests.push(request.url());
    }
  });

  await page.goto("/?page=traces", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(100);
  expect(traceRequests).toHaveLength(0);

  releaseWorkspace();
  await expect(page.getByTestId("traces-page")).toBeVisible();
  await expect.poll(() => traceRequests.length).toBe(1);
  expect(new URL(traceRequests[0]).pathname).not.toContain("/projects/default/");
});
