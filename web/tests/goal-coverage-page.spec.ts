import { expect, test, type Page, type Route } from "@playwright/test";

const feature = {
  id: "F-GOAL-EVAL",
  title: "Goal coverage evaluation",
  status: "in_progress",
  priority: 1,
};

const graph = {
  schema_version: "goal-coverage-graph.v1",
  coverage_mode: "explicit",
  identity: {
    project_id: "default",
    workflow_run_id: "RUN-EVAL-18",
    goal_id: "F-GOAL-EVAL",
    task_map_generation: "GEN-3",
    task_map_ref: ".zf/artifacts/F-GOAL-EVAL/task_map.json",
    goal_claim_set_digest: "claim-set-digest",
    target_commit: "abcdef123456",
  },
  currentness: { is_current_generation: true, superseded_by: "", stale_reasons: [] },
  summary: {
    mandatory_claims: 3,
    planned_claims: 2,
    claims_with_current_results: 1,
    closed_claims: 1,
    open_gaps: 1,
  },
  nodes: [
    { node_id: "goal:F-GOAL-EVAL", kind: "goal", title: "Ship deterministic authentication", goal_id: "F-GOAL-EVAL", status: "rejected" },
    { node_id: "claim:CLAIM-AUTH", kind: "goal_claim", goal_claim_id: "CLAIM-AUTH", title: "Unauthorized actions are rejected", mandatory: true, source_ref: "objective.acceptance[0]", plan_coverage: "covered", execution: "done", task_verification: "passed", closure: "closed", task_ids: ["TASK-AUTH"], supporting_result_refs: ["artifact://verify-auth"], gap_refs: [] },
    { node_id: "claim:CLAIM-REPLAY", kind: "goal_claim", goal_claim_id: "CLAIM-REPLAY", title: "Replay remains deterministic across restart", mandatory: true, source_ref: "objective.acceptance[1]", plan_coverage: "covered", execution: "running", task_verification: "stale", closure: "open", task_ids: ["TASK-AUTH", "TASK-REPLAY"], supporting_result_refs: [], gap_refs: ["artifact://gap-replay"] },
    { node_id: "claim:CLAIM-MIGRATION", kind: "goal_claim", goal_claim_id: "CLAIM-MIGRATION", title: "Existing projects migrate without manual state edits", mandatory: true, source_ref: "objective.acceptance[2]", plan_coverage: "uncovered", execution: "pending", task_verification: "unverified", closure: "open", task_ids: [], supporting_result_refs: [], gap_refs: ["artifact://gap-replay"] },
    { node_id: "task:TASK-AUTH", kind: "task", task_id: "TASK-AUTH", title: "Implement authorization boundary", status: "done", owner: "dev-core", contract_revision: "REV-2", goal_claim_ids: ["CLAIM-AUTH", "CLAIM-REPLAY"] },
    { node_id: "task:TASK-REPLAY", kind: "task", task_id: "TASK-REPLAY", title: "Verify restart replay", status: "in_progress", owner: "verify-1", contract_revision: "REV-3", goal_claim_ids: ["CLAIM-REPLAY"] },
    { node_id: "result:artifact://verify-auth", kind: "verification_result", task_id: "TASK-AUTH", title: "Authorization checks passed", status: "passed", result_ref: "artifact://verify-auth", evidence_refs: ["tests/test_auth.py"], current: true },
    { node_id: "closure:current", kind: "goal_closure", title: "Migration claim remains open", status: "rejected", result_ref: "current" },
    { node_id: "gap:artifact://gap-replay", kind: "gap", title: "artifact://gap-replay", status: "open", gap_ref: "artifact://gap-replay" },
  ],
  edges: [],
  diagnostics: [{ code: "mandatory_claim_uncovered", goal_claim_id: "CLAIM-MIGRATION", message: "mandatory claim has no covering task" }],
};

type DeliveryTraceResponder = (
  route: Route,
  body: Record<string, unknown>,
  requestNumber: number,
) => Promise<void>;

async function workTreeZoom(page: Page): Promise<number> {
  return page.locator(".delivery-work-canvas .react-flow__viewport").evaluate((element) => (
    new DOMMatrixReadOnly(window.getComputedStyle(element).transform).a
  ));
}

async function installFixture(
  page: Page,
  theme: "light" | "dark",
  deliveryTraceResponder?: DeliveryTraceResponder,
) {
  const milestoneEvents = Array.from({ length: 40 }, (_, index) => ({
    event_type: index % 3 === 0 ? "candidate.proposed" : index % 3 === 1 ? "verification.gate.completed" : "review.failed",
    task_id: index % 2 === 0 ? "TASK-AUTH" : "TASK-REPLAY",
    status: index % 3 === 2 ? "failed" : "done",
    ts: new Date(Date.parse("2026-07-22T04:20:01Z") + index * 200).toISOString(),
  }));
  const traceSpans = [
    { trace_id: `trace-${feature.id}`, span_id: "run:implementation", status: "running", started_at: "2026-07-22T04:20:01Z", duration_ms: 9000, raw_event_refs: ["evt-plan"] },
    { trace_id: `trace-${feature.id}`, span_id: "event:18", parent_span_id: "run:implementation", task_id: "TASK-AUTH", run_id: "dispatch-auth-2", status: "done", started_at: "2026-07-22T04:20:02Z", ended_at: "2026-07-22T04:20:10Z", duration_ms: 8000, evidence_refs: ["artifact://verify-auth"], raw_event_refs: ["evt-verify-2"] },
    ...Array.from({ length: 354 }, (_, index) => {
      const seq = index + 19;
      return {
        trace_id: `trace-${feature.id}`,
        span_id: `event:${seq}`,
        parent_span_id: "run:implementation",
        task_id: "TASK-AUTH",
        run_id: "dispatch-auth-2",
        status: index % 17 === 0 ? "failed" : "done",
        started_at: "2026-07-22T04:20:04Z",
        ended_at: "2026-07-22T04:20:05Z",
        duration_ms: 1000,
        raw_event_refs: [`evt-${seq}`],
      };
    }),
  ];
  await page.addInitScript((mode) => {
    window.localStorage.setItem("zf.themeMode", mode);
    (window as unknown as { __zfFullscreenCalls: number }).__zfFullscreenCalls = 0;
    const original = Element.prototype.requestFullscreen;
    Element.prototype.requestFullscreen = function requestFullscreen(...args) {
      (window as unknown as { __zfFullscreenCalls: number }).__zfFullscreenCalls += 1;
      return original.apply(this, args);
    };
  }, theme);
  await page.route("**/api/projects/*/delivery-features", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ delivery_features: [feature], features: [feature] }),
    });
  });
  let deliveryTraceRequestCount = 0;
  await page.route("**/api/projects/*/delivery-traces/*", async (route) => {
    const body: Record<string, unknown> = {
        schema_version: "delivery-trace.v1",
        feature_id: feature.id,
        trace_id: `trace-${feature.id}`,
        status: "in_progress",
        synthetic: false,
        cursor: { last_event_id: "evt-initial", new_event_count: 0 },
        diagnostics: [],
        ship: { status: "blocked" },
        drift_report: { status: "warning", summary: { warning: 1 }, items: [] },
        cycles: [{ cycle_id: "implementation", events: milestoneEvents }],
        run_chain: {
          schema_version: "run-chain.v1",
          status: "in_progress",
          trigger: { event_id: "evt-plan", type: "task_map.ready", actor: "planner", ts: "2026-07-22T04:20:00Z" },
          stages: [{
            stage: "implementation",
            status: "active",
            entered_at: "2026-07-22T04:20:01Z",
            via_event_id: "evt-plan",
            causation_id: "evt-plan",
            seq_first: 5,
            seq_last: 18,
            occurrences: 1,
            task_ids: ["TASK-AUTH", "TASK-REPLAY"],
          }],
        },
        task_lifecycle: {
          schema_version: "task-lifecycle.v1",
          tasks: {
            "TASK-AUTH": {
              state_history: [
                { state: "running", entered_at: "2026-07-22T04:20:02Z", try: 1 },
                { state: "failed", entered_at: "2026-07-22T04:20:05Z", try: 1 },
                { state: "running", entered_at: "2026-07-22T04:20:06Z", try: 2 },
                { state: "done", entered_at: "2026-07-22T04:20:10Z", try: 2 },
              ],
              tries: [
                { try: 1, outcome: "failed", dispatch_id: "dispatch-auth-1", first_response_seconds: 1, gate_results: [{ type: "verify", passed: false, event_id: "evt-verify-1" }] },
                { try: 2, outcome: "done", dispatch_id: "dispatch-auth-2", first_response_seconds: 1, rework_kind: "verify_rework", gate_results: [{ type: "verify", passed: true, event_id: "evt-verify-2" }] },
              ],
            },
            "TASK-REPLAY": {
              state_history: [{ state: "running", entered_at: "2026-07-22T04:20:11Z", try: 1 }],
              tries: [{ try: 1, outcome: "in_flight", dispatch_id: "dispatch-replay-1", gate_results: [] }],
            },
          },
        },
        flow_metrics: {
          tasks: {
            "TASK-AUTH": { queue_wait_seconds: 2, active_seconds: 8, backedge_count: 1 },
            "TASK-REPLAY": { queue_wait_seconds: 1, active_seconds: 4, backedge_count: 0 },
          },
        },
        trace: {
          schema_version: "delivery-run-trace.v1",
          trace_id: `trace-${feature.id}`,
          span_count: traceSpans.length,
          timeline_count: traceSpans.length,
          spans: traceSpans,
          timeline: [],
          usage_summary: {},
          autoresearch_graphs: [],
          diagnostics: [],
        },
        execution_graph: {
          task_count: 2,
          nodes: [
            { task_id: "TASK-AUTH", title: "Implement authorization boundary", planned: { wave: 1, blocked_by: [] }, actual: { status: "done", assigned_to: "dev-core" }, drift: [] },
            { task_id: "TASK-REPLAY", title: "Verify restart replay", planned: { wave: 2, blocked_by: ["TASK-AUTH"] }, actual: { status: "in_progress", assigned_to: "verify-1" }, drift: [] },
          ],
          edges: [{ from: "TASK-AUTH", to: "TASK-REPLAY", kind: "blocks", status: "satisfied" }],
          waves: [],
          diagnostics: [],
        },
        thick_trace: {
          graph: {
            node_count: 2,
            edge_count: 1,
            layers: ["plan", "runtime", "gate", "behavior", "eval", "artifact"],
            nodes: [
              { id: "task:TASK-AUTH", kind: "task", label: "Implement authorization boundary", status: "done", task_id: "TASK-AUTH" },
              { id: "eval:TASK-REPLAY", kind: "eval", label: "Restart verification", status: "failed", task_ids: ["TASK-REPLAY"] },
            ],
            edges: [{ id: "validated:TASK-REPLAY", source: "task:TASK-REPLAY", target: "eval:TASK-REPLAY", kind: "validated_by", status: "failed" }],
          },
          spans: [],
          span_count: 0,
          behaviors: [],
          evals: [],
          artifacts: [],
          improvement_candidates: [],
          diagnostics: [],
        },
        goal_coverage_graph: graph,
    };
    deliveryTraceRequestCount += 1;
    if (deliveryTraceResponder) {
      await deliveryTraceResponder(route, body, deliveryTraceRequestCount);
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  await page.route("**/api/projects/*/workflow/graph", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "workflow-graph.v1",
        nodes: [{
          id: "role:verify",
          kind: "role",
          label: "verify",
          pass_rate: 0.5,
          rework_count: 1,
          drill_task_id: "TASK-REPLAY",
        }],
        edges: [],
      }),
    });
  });
  await page.route("**/api/projects/*/regression-cases?*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        cases: [{
          case_id: "REG-REPLAY",
          source_task_id: "TASK-REPLAY",
          feature_id: feature.id,
          assertions: ["rework==0"],
        }],
      }),
    });
  });
}

async function openCoverage(page: Page) {
  await page.goto("/?page=goal-coverage");
  await expect(page.getByTestId("goal-coverage-page")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("goal-coverage-claim-row")).toHaveCount(3);
}

async function expectNoPageOverflow(page: Page) {
  const geometry = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
  }));
  expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth + 1);
  expect(geometry.clientWidth).toBeLessThanOrEqual(geometry.viewportWidth);
}

test("desktop light goal coverage supports selection, search, and focus", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await installFixture(page, "light");
  await openCoverage(page);

  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(page.getByTestId("goal-coverage-summary")).toContainText("2/3");
  await expect(page.getByLabel("Current generation")).toContainText("GEN-3");
  await expect(page.getByLabel("Current generation")).toContainText("current");
  await expect(page.getByLabel("Generation", { exact: true })).toHaveCount(0);
  await page.locator('[data-claim-id="CLAIM-AUTH"] .goal-coverage-claim-node').click();
  await expect(page.getByTestId("goal-coverage-inspector")).toContainText("Unauthorized actions are rejected");
  await expect(page.getByTestId("goal-coverage-inspector")).toContainText("artifact://verify-auth");
  await page.getByLabel("Search claims and tasks").fill("migration");
  await expect(page.getByTestId("goal-coverage-claim-row")).toHaveCount(1);
  await expect(page.getByTestId("goal-coverage-inspector")).toContainText("No covering task");

  await page.getByRole("button", { name: "Enter focus mode" }).click();
  const focused = page.getByTestId("goal-coverage-page");
  await expect(focused).toHaveClass(/is-focus/);
  const focusedBox = await focused.boundingBox();
  expect(focusedBox?.x).toBeLessThanOrEqual(1);
  expect(focusedBox?.width).toBeGreaterThanOrEqual(1438);
  await page.keyboard.press("Escape");
  await expect(focused).not.toHaveClass(/is-focus/);
  await expect(page.getByTestId("goal-coverage-inspector")).toContainText("CLAIM-MIGRATION");
  expect(await page.evaluate(() => (window as unknown as { __zfFullscreenCalls: number }).__zfFullscreenCalls)).toBe(0);
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("goal-coverage-desktop-light.png"), fullPage: true });

  await page.getByLabel("Search claims and tasks").fill("");
  await page.locator('[data-claim-id="CLAIM-AUTH"] .goal-coverage-claim-node').click();
  await page.getByTestId("goal-coverage-inspector").getByRole("button", { name: /Implement authorization boundary/ }).click();
  await expect(page).toHaveURL(/page=task/);
  await expect(page).toHaveURL(/task=TASK-AUTH/);
});

test("mobile dark goal coverage renders outline without horizontal overflow", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installFixture(page, "dark");
  await openCoverage(page);

  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  const firstRow = page.getByTestId("goal-coverage-claim-row").first();
  const tracks = await firstRow.evaluate((node) => getComputedStyle(node).gridTemplateColumns);
  expect(tracks.trim().split(/\s+/)).toHaveLength(1);
  await expect(firstRow.getByText("Claim", { exact: true })).toBeVisible();
  await expect(firstRow.getByText("Plan", { exact: true })).toBeVisible();
  await expect(firstRow.getByText("Implementation", { exact: true })).toBeVisible();
  await expect(firstRow.getByText("Verification", { exact: true })).toBeVisible();
  await expect(firstRow.getByText("Closure", { exact: true })).toBeVisible();
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("goal-coverage-mobile-dark.png"), fullPage: true });
});

test("Graph defaults to coverage and keeps work and diagnostics as lenses", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await installFixture(page, "light");
  await page.goto("/?page=delivery-graph");

  await expect(page.getByRole("heading", { name: "Graph", exact: true })).toBeVisible();
  const lenses = page.getByRole("tablist", { name: "Graph view" });
  await expect(lenses.getByRole("tab", { name: "Coverage" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("goal-coverage-claim-row")).toHaveCount(3);
  await expect(page.locator(".goal-coverage-task-line")).toHaveCount(0);
  await expect(page.locator(".goal-coverage-ref")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Goal Coverage", exact: true })).toHaveCount(0);

  await lenses.getByRole("tab", { name: "Work" }).click();
  await expect(page.getByTestId("delivery-map-work")).toBeVisible();
  await expect(page.getByTestId("delivery-work-canvas")).toBeVisible();
  await expect(page.getByRole("tablist", { name: "Work view" })).toHaveCount(0);
  await expect(page.getByText("Dependencies", { exact: true })).toHaveCount(0);
  await expect(page.locator('[data-work-kind="goal"]')).toHaveCount(1);
  await expect(page.locator('[data-work-kind="claim"]')).toHaveCount(3);
  await expect(page.locator('[data-work-kind="task"]')).toHaveCount(2);
  await expect(page.locator(".react-flow__edge")).toHaveCount(6);
  await expect(page.getByText("also covers", { exact: true })).toBeVisible();
  await expect.poll(() => workTreeZoom(page)).toBeGreaterThanOrEqual(0.99);
  await page.screenshot({ path: testInfo.outputPath("delivery-work-tree-default-desktop.png"), fullPage: true });

  await page.getByRole("button", { name: "Enter Work fullscreen" }).click();
  const fullscreenWork = page.getByTestId("delivery-map-work");
  await expect(fullscreenWork).toHaveClass(/is-focus/);
  const fullscreenBox = await fullscreenWork.boundingBox();
  expect(fullscreenBox?.x).toBeLessThanOrEqual(1);
  expect(fullscreenBox?.width).toBeGreaterThanOrEqual(1438);
  await page.screenshot({ path: testInfo.outputPath("delivery-work-tree-fullscreen-desktop.png"), fullPage: true });
  await page.keyboard.press("Escape");
  await expect(fullscreenWork).not.toHaveClass(/is-focus/);
  expect(await page.evaluate(() => (window as unknown as { __zfFullscreenCalls: number }).__zfFullscreenCalls)).toBe(0);

  const authClaim = page.locator('[data-work-node-id="claim:CLAIM-AUTH"]');
  await authClaim.getByRole("button", { name: /Collapse claim CLAIM-AUTH/ }).click();
  await expect(page.locator('[data-work-node-id="task:TASK-AUTH"]')).toHaveCount(0);
  await authClaim.getByRole("button", { name: /Expand claim CLAIM-AUTH/ }).click();
  await expect(page.locator('[data-work-node-id="task:TASK-AUTH"]')).toBeVisible();

  const zoomBeforeInspector = await workTreeZoom(page);
  await page.locator('[data-work-node-id="task:TASK-AUTH"]').click();
  const workInspector = page.getByTestId("delivery-work-inspector");
  await expect(workInspector).toContainText("Try #1");
  await expect(workInspector).toContainText("Try #2");
  await expect(workInspector).toContainText("Authorization checks passed");
  await expect.poll(async () => Math.abs(
    (await workTreeZoom(page)) - zoomBeforeInspector,
  )).toBeLessThan(0.01);

  await page.getByLabel("Search Work tree").fill("replay");
  await expect(page.locator('[data-work-node-id="claim:CLAIM-REPLAY"]')).toHaveClass(/is-match/);
  await expect(page.locator('[data-work-node-id="claim:CLAIM-REPLAY"]')).toHaveClass(/is-selected/);
  await page.getByLabel("Search Work tree").fill("");
  await page.locator('[data-work-node-id="task:TASK-AUTH"]').click();
  await page.getByRole("button", { name: "Fit Work tree" }).click();
  await page.waitForTimeout(350);
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("delivery-work-tree-desktop.png"), fullPage: true });

  await lenses.getByRole("tab", { name: "Diagnostics" }).click();
  await expect(page.getByTestId("delivery-thick-graph")).toBeVisible();
  await expect(page.getByTestId("delivery-quality-diagnostics")).toBeVisible();
  await expect(page.getByTestId("graph-stage-heatmap")).toHaveCount(0);
  await expect(page.getByTestId("regression-cases")).toContainText("REG-REPLAY");

  await lenses.getByRole("tab", { name: "Coverage" }).click();
  await page.locator('[data-claim-id="CLAIM-REPLAY"] .goal-coverage-open-work').click();
  await expect(lenses.getByRole("tab", { name: "Work" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator('[data-work-node-id="claim:CLAIM-REPLAY"]')).toHaveClass(/is-selected/);
  await expect(page.getByTestId("delivery-work-inspector")).toContainText("CLAIM-REPLAY");
});

test("Delivery Graph ignores an older poll that resolves after a newer response", async ({ page }) => {
  test.setTimeout(25_000);
  await page.setViewportSize({ width: 1440, height: 960 });
  await installFixture(page, "light", async (route, body, requestNumber) => {
    const next = structuredClone(body);
    const coverage = structuredClone(graph);
    const goalTitle = requestNumber >= 3
      ? "Newest accepted goal"
      : requestNumber === 2
        ? "Older delayed goal"
        : "Initial goal";
    coverage.nodes = coverage.nodes.map((node) => (
      node.kind === "goal" ? { ...node, title: goalTitle } : node
    ));
    next.goal_coverage_graph = coverage;
    next.cursor = {
      last_event_id: `evt-${requestNumber}`,
      new_event_count: requestNumber > 1 ? 1 : 0,
    };
    if (requestNumber === 2) {
      await new Promise((resolve) => setTimeout(resolve, 6500));
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(next),
    });
  });
  await page.goto("/?page=delivery-graph");

  const goalNode = page.getByTestId("goal-coverage-goal-node");
  await expect(goalNode).toContainText("Initial goal");
  await expect(goalNode).toContainText("Newest accepted goal", { timeout: 15_000 });
  await page.waitForTimeout(2500);
  await expect(goalNode).toContainText("Newest accepted goal");
  await expect(goalNode).not.toContainText("Older delayed goal");
});

test("Runs keeps Run and Spans with stage quality and task attempts in context", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await installFixture(page, "light");
  await page.goto("/?page=delivery-trace");

  await expect(page.getByRole("heading", { name: "Runs", exact: true })).toBeVisible();
  const modeTabs = page.getByTestId("dt-mode-tabs");
  await expect(modeTabs.getByRole("button")).toHaveText(["Overview", "Runs", "Graph"]);
  const tabs = page.getByRole("tablist", { name: "Runs views" });
  await expect(tabs.getByRole("tab")).toHaveCount(2);
  await expect(tabs.getByRole("tab", { name: /^Run/ })).toHaveAttribute("aria-selected", "true");
  await expect(tabs.getByRole("tab", { name: /^Spans/ })).toBeVisible();
  await expect(page.getByTestId("delivery-graph")).toHaveCount(0);
  await expect(page.getByTestId("delivery-flow-context")).toHaveCount(0);
  await expect(page.getByTestId("run-graph")).toBeVisible();
  await expect(page.getByTestId("graph-stage-heatmap")).toContainText("verify");
  const heatmapBox = await page.getByTestId("graph-stage-heatmap").boundingBox();
  const runGraphBox = await page.getByTestId("run-graph").boundingBox();
  expect(heatmapBox?.y).toBeLessThan(runGraphBox?.y ?? 0);

  await page.getByTestId("graph-stage-heatmap-row").click();
  await expect(page.getByTestId("lifecycle-drawer")).toHaveAttribute("aria-label", "Task lifecycle TASK-REPLAY");
  await page.getByRole("button", { name: "close lifecycle drawer" }).click();

  const authTask = page.getByTestId("rg-task-TASK-AUTH");
  await expect(authTask).toContainText("try×2");
  await authTask.click();
  await expect(page.getByTestId("lifecycle-drawer")).toBeVisible();
  await expect(page.getByTestId("ld-tries")).toContainText("#1");
  await expect(page.getByTestId("ld-tries")).toContainText("#2");
  await expect(page.locator(".ld-gate")).toHaveCount(2);
  await page.screenshot({ path: testInfo.outputPath("delivery-runs-desktop.png"), fullPage: true });

  await tabs.getByRole("tab", { name: /^Spans/ }).click();
  await expect(page.getByTestId("delivery-trace-tab")).toBeVisible();
  await expect(page.getByTestId("graph-stage-heatmap")).toHaveCount(0);
  await expect(page.getByTestId("wf-panel-head").getByRole("heading", { name: "Spans", exact: true })).toBeVisible();
  await expect(page.getByTestId("wf-span-total")).toHaveText("356 spans");
  await expect(page.getByTestId("wf-span-hierarchy")).toHaveText("feature → phase → task → try → event");
  await expect(page.getByTestId("dt-span-inspector")).toHaveCount(0);
  const spansLayoutBox = await page.getByTestId("delivery-trace-tab").boundingBox();
  const spansPanelBox = await page.getByTestId("dt-flow-span-tree").boundingBox();
  expect(spansPanelBox?.width).toBeGreaterThanOrEqual((spansLayoutBox?.width ?? 0) - 1);
  const axisGap = await page.getByTestId("wf-axis").locator(".wf-axis-label").evaluate((node) => {
    const [label, clock] = node.children;
    return clock.getBoundingClientRect().left - label.getBoundingClientRect().right;
  });
  expect(axisGap).toBeGreaterThanOrEqual(10);
  const axisTickState = await page.getByTestId("wf-axis").locator(".wf-axis-track").evaluate((track) => {
    const ticks = [...track.querySelectorAll<HTMLElement>(".wf-axis-tick")];
    const visibleLabels = ticks
      .map((tick) => tick.querySelector<HTMLElement>("small"))
      .filter((label): label is HTMLElement => label !== null && getComputedStyle(label).opacity === "1")
      .map((label) => label.getBoundingClientRect())
      .sort((left, right) => left.left - right.left);
    const trackBox = track.getBoundingClientRect();
    const axisRowBox = track.closest(".wf-row")!.getBoundingClientRect();
    const rightLabels = ticks
      .filter((tick) => tick.classList.contains("is-labelled") && tick.classList.contains("is-right-side"))
      .map((tick) => ({
        label: tick.querySelector<HTMLElement>("small")!.getBoundingClientRect(),
        marker: tick.getBoundingClientRect(),
      }));
    return {
      tickCount: ticks.length,
      trackOffset: trackBox.left - axisRowBox.left,
      labelledCount: visibleLabels.length,
      labelsOverlap: visibleLabels.some((label, index) => index > 0 && label.left < visibleLabels[index - 1].right),
      labelsInsideTrack: visibleLabels.every((label) => label.left >= trackBox.left && label.right <= trackBox.right),
      rightLabelsOpenLeft: rightLabels.every(({ label, marker }) => label.right <= marker.left + marker.width / 2 + 1),
      trackBounds: { left: trackBox.left, right: trackBox.right },
      labelBounds: visibleLabels.map((label) => ({ left: label.left, right: label.right })),
      firstTick: ticks[0] ? {
        className: ticks[0].className,
        left: ticks[0].style.left,
        transform: getComputedStyle(ticks[0]).transform,
      } : null,
    };
  });
  expect(axisTickState.tickCount).toBeGreaterThan(0);
  expect(axisTickState.trackOffset).toBeLessThan(340);
  expect(axisTickState.labelledCount).toBeGreaterThan(0);
  expect(axisTickState.labelledCount).toBeLessThan(axisTickState.tickCount);
  expect(axisTickState.labelsOverlap).toBe(false);
  expect(axisTickState.labelsInsideTrack, JSON.stringify(axisTickState)).toBe(true);
  expect(axisTickState.rightLabelsOpenLeft, JSON.stringify(axisTickState)).toBe(true);
  const hiddenAxisTick = page.getByTestId("wf-axis").locator(".wf-axis-tick:not(.is-labelled)").first();
  await expect(hiddenAxisTick.locator("small")).toHaveCSS("opacity", "0");
  await hiddenAxisTick.hover();
  await expect(hiddenAxisTick.locator("small")).toHaveCSS("opacity", "1");
  const taskLabelsOverlap = await page.locator(".wf-task .wf-row-label").evaluateAll((labels) => labels.some((label) => {
    const children = [...label.children].map((child) => child.getBoundingClientRect());
    return children.some((child, index) => index > 0 && child.left < children[index - 1].right);
  }));
  expect(taskLabelsOverlap).toBe(false);
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("delivery-spans-desktop.png"), fullPage: true });
});

test("mobile Runs keeps the Run and Spans workbench inside the viewport", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installFixture(page, "dark");
  await page.goto("/?page=delivery-trace");

  const tabs = page.getByRole("tablist", { name: "Runs views" });
  await expect(tabs.getByRole("tab")).toHaveCount(2);
  await expect(page.getByTestId("run-graph")).toBeVisible();
  await expect(page.getByTestId("graph-stage-heatmap")).toBeVisible();
  await expectNoPageOverflow(page);
  await tabs.getByRole("tab", { name: /^Spans/ }).click();
  await expect(page.getByTestId("delivery-trace-tab")).toBeVisible();
  await expect(page.getByTestId("wf-span-total")).toHaveText("356 spans");
  await expect(page.getByTestId("wf-span-hierarchy")).toBeVisible();
  await expect(page.getByTestId("dt-span-inspector")).toHaveCount(0);
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("delivery-runs-mobile.png"), fullPage: true });
});

test("Runs omits Stage Heatmap when workflow graph has no role projection", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await installFixture(page, "light");
  await page.route("**/api/projects/*/workflow/graph", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ schema_version: "workflow-graph.v1", nodes: [], edges: [] }),
    });
  });
  await page.goto("/?page=delivery-trace");

  await expect(page.getByTestId("run-graph")).toBeVisible();
  await expect(page.getByTestId("graph-stage-heatmap")).toHaveCount(0);
});

test("mobile Graph coverage has no page overflow", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installFixture(page, "dark");
  await page.goto("/?page=delivery-graph");

  await expect(page.getByTestId("delivery-map")).toBeVisible();
  await expect(page.getByTestId("goal-coverage-claim-row")).toHaveCount(3);
  await expectNoPageOverflow(page);

  await page.getByRole("tablist", { name: "Graph view" }).getByRole("tab", { name: "Work" }).click();
  await expect(page.getByTestId("delivery-work-canvas")).toBeVisible();
  const mobileCanvas = await page.getByTestId("delivery-work-canvas").boundingBox();
  expect(mobileCanvas?.height).toBeGreaterThanOrEqual(300);
  await expect(page.locator('[data-work-kind="task"]')).toHaveCount(2);
  await page.getByRole("button", { name: "Enter Work fullscreen" }).click();
  const mobileFullscreenWork = page.getByTestId("delivery-map-work");
  const mobileFullscreenBox = await mobileFullscreenWork.boundingBox();
  expect(mobileFullscreenBox?.x).toBeLessThanOrEqual(1);
  expect(mobileFullscreenBox?.width).toBeGreaterThanOrEqual(389);
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("delivery-work-tree-fullscreen-mobile.png"), fullPage: true });
  await page.keyboard.press("Escape");
  await expect(mobileFullscreenWork).not.toHaveClass(/is-focus/);
  await page.locator('[data-work-node-id="task:TASK-AUTH"]').click();
  await expect(page.getByTestId("delivery-work-inspector")).toBeVisible();
  await expect(page.getByTestId("delivery-work-try")).toHaveCount(2);
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("delivery-work-tree-mobile.png"), fullPage: true });
});
