import { expect, test, type Page } from "@playwright/test";

function loopViewWithAttempts(attemptCount: number) {
  const attempts = Array.from({ length: attemptCount }, (_, index) => {
    const startedAt = new Date(Date.UTC(2026, 7, 20, 0, index));
    const terminalAt = new Date(startedAt.getTime() + 30_000);
    return {
      started_ts: startedAt.toISOString(),
      role: `implementer-${index % 4}`,
      terminal: {
        type: index % 5 === 0 ? "dev.failed" : "dev.completed",
        ts: terminalAt.toISOString(),
        seq: index + 2,
      },
      open: false,
      counted: true,
    };
  });
  const fails = Math.ceil(attemptCount / 5);
  return {
    schema_version: "loop-view.v1",
    generated_at: "2026-08-20T16:00:00Z",
    project_id: "loop-browser-test",
    scope: {
      kind: "business_delivery",
      business_delivery_active: true,
      reason: "delivery evidence present",
    },
    run: {
      run_id: "run-loop-browser-test",
      scope: "current",
      event_count: attemptCount * 2,
      semantic_event_count: attemptCount * 2,
      first_ts: attempts[0]?.started_ts ?? "",
      last_ts: attempts.at(-1)?.terminal.ts ?? "",
      latched: false,
      promise: {
        source: "workflow_completion contract",
        satisfied: 2,
        latched: false,
        chain: [
          { event: "plan.approved", satisfied: true, seq: 1 },
          { event: "run.completed", satisfied: false },
        ],
      },
    },
    stages: [
      { id: "plan", rounds: 1, last_status: "plan.approved", last_ts: "2026-08-20T00:00:00Z", warn: false },
      { id: "implementation", rounds: attemptCount, last_status: "dev.completed", last_ts: "2026-08-20T14:34:30Z", warn: true },
      { id: "verification", rounds: 1, last_status: "verify.started", last_ts: "2026-08-20T14:35:00Z", warn: false },
    ],
    backflows: [{ from_stage: "verification", to_stage: "implementation", kind: "rework", count: fails }],
    subscriber_chains: [{ topic: "plan.approved", seq: 1, subscriber: "implementation", result: "TASK-HIGH-CHURN", result_seq: 2 }],
    tasks: [{
      id: "TASK-HIGH-CHURN",
      stage_id: "implementation",
      attempts,
      fails,
      counted: attemptCount,
      source: "task_attempts.json",
    }],
    loops: {
      delivery: {
        id: "delivery",
        label: "Delivery",
        shape: ["plan", "task-map", "impl", "verify", "ship"],
        closure_edge: ["verify", "impl"],
        counts: { tasks: 1, attempts: attemptCount, rejects: fails },
        arc: { state: "active", label: `${fails} backflows` },
        health: "diverging",
        members: [{ kind: "task", id: "TASK-HIGH-CHURN", note: `${attemptCount} att · ${fails}✗` }],
        node_stats: { impl: { attempts: attemptCount }, verify: { rejects: fails } },
      },
    },
    faults: [{ kind: "verify.failed", count: fails, owner_loop: "delivery" }],
    companions: {},
    pump: { total: 50_000, lag_warnings: 0 },
    health_counters: {},
    source_projection_refs: ["task_attempts.json", "EventLog"],
  };
}

async function installLoopStreamHarness(page: Page): Promise<void> {
  await page.addInitScript(() => {
    let visible = true;
    let latest: FakeEventSource | null = null;

    class FakeEventSource {
      onerror: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onopen: ((event: Event) => void) | null = null;
      private readonly listeners = new Map<string, Array<(event: MessageEvent) => void>>();

      constructor(_url: string) {
        latest = this;
        window.setTimeout(() => this.onopen?.(new Event("open")), 0);
      }

      addEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
        const callback = typeof listener === "function"
          ? (listener as (event: MessageEvent) => void)
          : (event: MessageEvent) => listener.handleEvent(event);
        this.listeners.set(type, [...(this.listeners.get(type) ?? []), callback]);
      }

      close(): void {}

      emit(payload: Record<string, unknown>): void {
        const message = new MessageEvent("message", {
          data: JSON.stringify(payload),
          lastEventId: String(payload.seq ?? ""),
        });
        this.onmessage?.(message);
      }

      emitMalformed(): void {
        this.onmessage?.(new MessageEvent("message", { data: "{" }));
      }

      fail(): void {
        this.onerror?.(new Event("error"));
      }

      reopen(): void {
        this.onopen?.(new Event("open"));
      }
    }

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => (visible ? "visible" : "hidden"),
    });
    Object.defineProperty(window, "EventSource", { configurable: true, value: FakeEventSource });
    Object.assign(window, {
      __loopStream: {
        emit: (type: string, seq: number) => latest?.emit({
          id: `event-${seq}`,
          seq,
          ts: new Date(Date.UTC(2026, 7, 20, 16, 0, seq)).toISOString(),
          type,
        }),
        fail: () => latest?.fail(),
        malformed: () => latest?.emitMalformed(),
        reopen: () => latest?.reopen(),
        status: () => ({ hasLatest: Boolean(latest), hasOnmessage: Boolean(latest?.onmessage), visible }),
        setVisible: (nextVisible: boolean) => {
          visible = nextVisible;
          document.dispatchEvent(new Event("visibilitychange"));
        },
      },
    });
  });
}

async function mockLoopBootstrap(page: Page): Promise<void> {
  const project = (projectId: string) => ({
    project_id: projectId,
    name: projectId,
    root: `/tmp/${projectId}`,
    config_path: `/tmp/${projectId}/zf.yaml`,
    state_dir_hint: `.zf`,
    state_dir_resolved: `/tmp/${projectId}/.zf`,
    can_open_board: true,
    lifecycle: {
      has_config: true,
      config_loadable: true,
      state_dir_exists: true,
      initialized: true,
      can_open_board: true,
      runtime_state: "running",
    },
  });
  const projects = [project("loop-browser-test"), project("loop-browser-test-2")];
  await page.route(/\/api\/workspace\/projects$/, (route) => route.fulfill({
    json: {
      active_project_id: "loop-browser-test",
      items: projects,
      projects,
      server_default_project_id: "loop-browser-test",
    },
  }));
  await page.route(/\/api\/workspace\/onboarding$/, (route) => route.fulfill({
    json: { completed: true, skipped: false },
  }));
  await page.route(/\/api\/projects\/[^/]+\/health\/summary$/, (route) => route.fulfill({
    json: {
      schema_version: "project-health.v1",
      runtime_state: "running",
      live: true,
      seq: 0,
      last_event_age_s: 0,
      task_counts: {},
      active: 0,
      queued: 0,
      blocked: 0,
      projection: { state: "ready", lag: 0, tail_behind: false },
    },
  }));
}

test("Loop owns its request, bounds attempt DOM, and fits a 368px container", async ({ page }) => {
  test.setTimeout(120_000);
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.stack ?? error.message));
  await page.setViewportSize({ width: 368, height: 844 });
  await installLoopStreamHarness(page);
  await mockLoopBootstrap(page);
  let deliveryFeatureRequests = 0;
  let loopViewRequests = 0;
  let activeLoopRequests = 0;
  let maxActiveLoopRequests = 0;
  let delayNextLoopResponse = false;
  let releaseDelayedResponse: (() => void) | null = null;
  const loopRequestProjects: string[] = [];

  await page.route("**/api/projects/*/delivery-features", async (route) => {
    deliveryFeatureRequests += 1;
    await route.fulfill({ json: { delivery_features: [], features: [] } });
  });
  await page.route("**/api/projects/*/loop-view", async (route) => {
    loopViewRequests += 1;
    activeLoopRequests += 1;
    maxActiveLoopRequests = Math.max(maxActiveLoopRequests, activeLoopRequests);
    loopRequestProjects.push(new URL(route.request().url()).pathname.split("/")[3] ?? "");
    try {
      if (delayNextLoopResponse) {
        delayNextLoopResponse = false;
        await new Promise<void>((resolve) => { releaseDelayedResponse = resolve; });
      }
      await route.fulfill({ json: loopViewWithAttempts(875) });
    } finally {
      activeLoopRequests -= 1;
    }
  });

  await page.goto("/?page=behavior-loop&project=loop-browser-test");
  const loopPage = page.getByTestId("loop-page-v2");
  await expect(loopPage).toBeVisible({ timeout: 90_000 });
  await expect(loopPage.getByText("875 legacy records", { exact: false })).toBeVisible();
  const timelineTypography = await loopPage.getByTestId("loop-task-row").first().evaluate((row) => ({
    label: getComputedStyle(row.querySelector<HTMLElement>(".loop-v2-timeline-label")!).fontWeight,
    meta: getComputedStyle(row.querySelector<HTMLElement>(".loop-v2-timeline-meta")!).fontWeight,
  }));
  expect(timelineTypography).toEqual({ label: "500", meta: "400" });
  await page.waitForTimeout(250);
  expect(loopViewRequests).toBe(1);
  expect(deliveryFeatureRequests).toBe(0);
  expect(await page.evaluate(() => (window as unknown as {
    __loopStream: { status: () => { hasLatest: boolean; hasOnmessage: boolean; visible: boolean } };
  }).__loopStream.status())).toEqual({ hasLatest: true, hasOnmessage: true, visible: true });
  await page.waitForTimeout(1_600);
  expect(loopViewRequests).toBe(1);

  await page.getByTestId("loop-chip-delivery").click();
  await page.getByTestId("loop-member").click();
  const drawer = page.getByTestId("loop-attempt-drawer");
  await expect(drawer).toBeVisible();
  await expect(drawer.getByTestId("loop-attempt-count")).toHaveText("Showing 100 of 875 records");
  await expect(drawer.getByTestId("loop-attempt-row")).toHaveCount(100);
  expect(await drawer.locator("*").count()).toBeLessThan(600);

  await drawer.getByTestId("loop-attempt-load-more").click();
  await expect(drawer.getByTestId("loop-attempt-count")).toHaveText("Showing 200 of 875 records");
  await expect(drawer.getByTestId("loop-attempt-row")).toHaveCount(200);

  await page.getByTestId("loop-task-row").getByRole("button", { name: /TASK-HIGH-CHURN/ }).click();
  await expect(drawer).toHaveCount(0);
  await page.getByTestId("loop-member").click();
  await expect(page.getByTestId("loop-attempt-row")).toHaveCount(100);

  const widths = await page.evaluate(() => {
    const metrics = (selector: string) => {
      const element = document.querySelector<HTMLElement>(selector);
      return element ? { clientWidth: element.clientWidth, scrollWidth: element.scrollWidth } : null;
    };
    return {
      loop: metrics("[data-testid=loop-page-v2]"),
      projection: metrics(".projection-scroll"),
    };
  });
  expect(widths.loop).not.toBeNull();
  expect(widths.projection).not.toBeNull();
  expect(widths.loop!.scrollWidth).toBeLessThanOrEqual(widths.loop!.clientWidth + 1);
  expect(widths.projection!.scrollWidth).toBeLessThanOrEqual(widths.projection!.clientWidth + 1);

  await page.evaluate(() => (window as unknown as {
    __loopStream: { emit: (type: string, seq: number) => void };
  }).__loopStream.emit("worker.heartbeat", 1));
  await page.evaluate(() => {
    const harness = (window as unknown as { __loopStream: { emit: (type: string, seq: number) => void } }).__loopStream;
    harness.emit("task.attempt.heartbeat", 2);
    harness.emit("run.heartbeat", 3);
  });
  await page.waitForTimeout(1_700);
  expect(loopViewRequests).toBe(1);

  await page.evaluate(() => {
    const harness = (window as unknown as { __loopStream: { emit: (type: string, seq: number) => void } }).__loopStream;
    harness.emit("custom.profile.check.completed", 4);
    harness.emit("verify.failed", 5);
    harness.emit("task.dispatched", 6);
    harness.emit("worker.heartbeat", 7);
  });
  await page.waitForTimeout(100);
  expect(pageErrors).toEqual([]);
  await expect(loopPage).toHaveAttribute("data-live-event-type", "worker.heartbeat");
  await expect.poll(() => loopViewRequests, { timeout: 3_500 }).toBe(2);

  await page.evaluate(() => (window as unknown as { __loopStream: { setVisible: (visible: boolean) => void } }).__loopStream.setVisible(false));
  await page.evaluate(() => (window as unknown as { __loopStream: { emit: (type: string, seq: number) => void } }).__loopStream.emit("judge.failed", 8));
  await page.waitForTimeout(1_700);
  expect(loopViewRequests).toBe(2);
  await page.evaluate(() => (window as unknown as { __loopStream: { setVisible: (visible: boolean) => void } }).__loopStream.setVisible(true));
  await expect.poll(() => loopViewRequests, { timeout: 3_500 }).toBe(3);

  await page.evaluate(() => (window as unknown as { __loopStream: { fail: () => void; reopen: () => void } }).__loopStream.fail());
  await page.waitForTimeout(50);
  await page.evaluate(() => (window as unknown as { __loopStream: { reopen: () => void } }).__loopStream.reopen());
  await expect.poll(() => loopViewRequests, { timeout: 3_500 }).toBe(4);

  await page.evaluate(() => (window as unknown as { __loopStream: { malformed: () => void } }).__loopStream.malformed());
  await page.waitForTimeout(50);
  await page.evaluate(() => (window as unknown as { __loopStream: { reopen: () => void } }).__loopStream.reopen());
  await expect.poll(() => loopViewRequests, { timeout: 3_500 }).toBe(5);

  delayNextLoopResponse = true;
  await page.evaluate(() => (window as unknown as { __loopStream: { emit: (type: string, seq: number) => void } }).__loopStream.emit("dev.completed", 9));
  await expect.poll(() => loopViewRequests, { timeout: 3_500 }).toBe(6);
  await page.evaluate(() => (window as unknown as { __loopStream: { emit: (type: string, seq: number) => void } }).__loopStream.emit("test.passed", 10));
  await page.waitForTimeout(1_700);
  expect(loopViewRequests).toBe(6);
  expect(maxActiveLoopRequests).toBe(1);
  expect(releaseDelayedResponse).not.toBeNull();
  releaseDelayedResponse!();
  await expect.poll(() => loopViewRequests, { timeout: 4_000 }).toBe(7);
  expect(maxActiveLoopRequests).toBe(1);

  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByLabel("Project").selectOption("loop-browser-test-2");
  await page.getByRole("button", { name: "Loop", exact: true }).click();
  await expect.poll(() => loopRequestProjects.includes("loop-browser-test-2"), { timeout: 4_000 }).toBe(true);
  expect(deliveryFeatureRequests).toBe(0);
});

test("Loop timeline aligns time, selects original attempts and keeps unknown durations honest", async ({ page }, testInfo) => {
  const view = loopViewWithAttempts(3);
  view.run.last_ts = "2026-08-20T00:20:00Z";
  view.tasks[0].attempts[0].terminal.ts = "2026-08-20T00:05:00Z";
  view.tasks[0].attempts[1].started_ts = "2026-08-20T00:02:00Z";
  view.tasks[0].attempts[1].terminal.ts = "2026-08-20T00:04:00Z";
  Object.assign(view.tasks[0].attempts[2], { started_ts: "2026-08-20T00:15:00Z", orphan: true });
  view.tasks[0].attempts[2].terminal.ts = "2026-08-20T00:15:00Z";
  await installLoopStreamHarness(page);
  await mockLoopBootstrap(page);
  await page.route("**/api/projects/*/loop-view", (route) => route.fulfill({ json: view }));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/?page=behavior-loop&project=loop-browser-test");
  const timeline = page.getByTestId("loop-timeline");
  await expect(timeline.getByRole("button", { name: "Real time", exact: true })).toHaveAttribute("aria-pressed", "true");
  const axis = await page.getByTestId("loop-time-axis").boundingBox();
  const track = await page.locator(".loop-v2-timeline-track").first().boundingBox();
  expect(Math.abs(axis!.x - track!.x)).toBeLessThan(1);
  expect(Math.abs(axis!.width - track!.width)).toBeLessThan(1);
  await timeline.getByRole("button", { name: /^Attempt 2 ·/ }).focus();
  await page.keyboard.press("Enter");
  const drawer = page.getByTestId("loop-attempt-drawer");
  await expect(drawer).toContainText("Attempt #2");
  await expect(drawer.getByTestId("loop-attempt-row")).toHaveCount(1);
  await expect(drawer).toContainText("2m 0s");
  const original = view.tasks[0];
  original.attempts = [original.attempts[2], original.attempts[0], original.attempts[1]];
  view.tasks = [{ ...original, id: "NEW-TASK", attempts: [], counted: 0, fails: 0 }, original];
  await page.evaluate(() => (window as unknown as {
    __loopStream: { emit: (type: string, seq: number) => void };
  }).__loopStream.emit("custom.profile.check.completed", 20));
  await expect(drawer).toContainText("Attempt #3");
  await expect(drawer).toContainText("implementer-1");
  await expect(drawer).toContainText("2m 0s");
  await timeline.getByRole("button", { name: /^Attempt 1 ·/ }).click();
  await expect(drawer).toContainText("Duration unknown");
  await expect(drawer).toContainText("Start unknown");
  await drawer.getByRole("button", { name: "View all 3 records" }).click();
  await expect(drawer.getByTestId("loop-attempt-row")).toHaveCount(3);
  await timeline.getByRole("button", { name: "Compress gaps" }).click();
  await expect(timeline).toContainText("Compressed scale");
  await timeline.getByRole("button", { name: "Real time", exact: true }).click();
  await timeline.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("loop-desktop.png") });
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
  await page.screenshot({ path: testInfo.outputPath("loop-dark.png") });
  await page.setViewportSize({ width: 368, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(368);
  await timeline.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("loop-mobile.png") });
  view.tasks = [];
  await page.reload();
  await expect(timeline).toContainText("No observed attempts or related events in this run");
  await expect(page.getByTestId("loop-time-axis")).toHaveCount(0);
});

test("identity-bound attempts, unpaired events and admitted plan remain distinct", async ({ page }, testInfo) => {
  await installLoopStreamHarness(page);
  await mockLoopBootstrap(page);
  const legacy = loopViewWithAttempts(2);
  const task = { ...legacy.tasks[0], attempts: [{ ...legacy.tasks[0].attempts[0], attempt_id: "att-1" }],
    counted: 1, fails: 1, source: "identity-bound events", signals: [
      { task_id: "TASK-HIGH-CHURN", type: "web.action.completed", ts: "2026-08-20T00:04:00Z", seq: 4, reason: "unpaired" },
      { task_id: "TASK-HIGH-CHURN", type: "worker.context.compact.failed", ts: "2026-08-20T00:05:00Z", seq: 5, reason: "unpaired" },
    ] };
  const view = { ...legacy, timeline: { tasks: [task], attempt_count: 1, failed_attempt_count: 1,
    signal_count: 2, source: "identity-bound events", coverage: "observed_only", baseline: {
      event_id: "admitted-1", seq: 9, task_map_ref: "artifact:plan", task_map_digest: "a".repeat(64),
      task_ids: ["TASK-HIGH-CHURN", "TASK-NOT-OBSERVED"],
    } } };
  await page.route("**/api/projects/*/loop-view", route => route.fulfill({ json: view }));
  await page.goto("/?page=behavior-loop&project=loop-browser-test&request=req-1&expected_run=wrong-run&task=TASK-HIGH-CHURN");
  await expect(page.getByRole("alert")).toContainText("不一致");
  await expect(page.getByTestId("loop-attempt-drawer")).toHaveCount(0);
  await page.goto("/?page=behavior-loop&project=loop-browser-test&request=req-1&expected_run=run-loop-browser-test&task=TASK-HIGH-CHURN");
  const timeline = page.getByTestId("loop-timeline");
  await expect(timeline).toContainText("Observed attempts 1");
  await expect(timeline).toContainText("Failed attempts 1");
  await expect(timeline).toContainText("Other events 2");
  const comparison = page.getByTestId("loop-plan-comparison");
  await comparison.locator("summary").click();
  await expect(comparison).toContainText("TASK-NOT-OBSERVED");
  await expect(comparison).toContainText("尚未观察到尝试");
  await expect(comparison).toContainText("ETA 和成本预测未显示");
  await expect(page.getByTestId("loop-attempt-drawer")).toContainText("web.action.completed");
  await expect(page.getByRole("link", { name: "返回 Workflow" })).toHaveAttribute("href", /request=req-1/);
  await timeline.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("identity-timeline.png") });
});
