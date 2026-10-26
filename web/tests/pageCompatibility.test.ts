import {
  canBootstrapScopedPageBeforeWorkspace,
  projectionPageQuery,
  projectionSelectionForPage,
  resolvePageCompatibility,
} from "../src/app/pageCompatibility.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function resolve(query: string) {
  return resolvePageCompatibility(new URLSearchParams(query));
}

function testTraceAliasesBecomeCanonicalBeforeBootstrap(): void {
  for (const query of [
    "project=p1&page=observability",
    "project=p1&page=observability&obs_tab=traces",
    "project=p1&page=workdirs",
    "project=p1&page=skills",
    "project=p1&page=archives",
  ]) {
    const result = resolve(query);
    assert(result.page === "traces", `${query} should resolve to traces`);
    assert(result.params.get("project") === "p1", `${query} should keep project scope`);
    assert(!result.params.has("obs_tab"), `${query} should drop the legacy tab`);
  }

  const selected = resolve("page=observability&obs_tab=traces&obs_trace=trace-old");
  assert(selected.params.get("trace_id") === "trace-old", "legacy trace id should migrate");
  assert(!selected.params.has("obs_trace"), "legacy trace key should be removed");

  const canonicalWins = resolve("page=traces&trace_id=trace-new&obs_trace=trace-old");
  assert(canonicalWins.params.get("trace_id") === "trace-new", "canonical trace id should win");
}

function testGoalCoverageAliasKeepsSelection(): void {
  const result = resolve("project=p1&page=goal-coverage&feature=FEATURE-1");
  assert(result.page === "delivery-graph", "legacy goal coverage should resolve to Delivery Graph");
  assert(result.params.get("project") === "p1", "goal coverage alias should keep project scope");
  assert(result.params.get("feature") === "FEATURE-1", "goal coverage alias should keep feature selection");
}

function testEntityAndEvidenceAliasesKeepTheirContext(): void {
  const cases = [
    ["events", "events"],
    ["runs", "runs"],
    ["fanouts", "fanouts"],
    ["candidates", "candidates"],
  ] as const;
  for (const [tab, page] of cases) {
    const result = resolve(`page=observability&obs_tab=${tab}`);
    assert(result.page === page, `${tab} should resolve to its entity page`);
  }

  const event = resolve("page=observability&obs_tab=events&obs_task=TASK-1");
  assert(event.params.get("task") === "TASK-1", "legacy event task should migrate");
  const run = resolve("page=observability&obs_tab=runs&obs_run_id=run-1");
  assert(run.params.get("run_id") === "run-1", "legacy run id should migrate");
}

function testRetiredDiagnosticsRoutesHaveBoundedFallbacks(): void {
  for (const query of [
    "page=diagnostics",
    "page=observability&obs_tab=raw",
    "page=observability&obs_tab=diagnostics",
  ]) {
    const result = resolve(query);
    assert(result.page === "events", `${query} should resolve to event evidence`);
  }

  for (const query of [
    "page=runtime",
    "page=control-room",
    "page=process",
    "page=observability&obs_tab=runtime_logs",
  ]) {
    const result = resolve(query);
    assert(result.page === "observability", `${query} should preserve Operations compatibility`);
    assert(result.params.get("obs_tab") === "operations", `${query} should select Operations`);
  }
}

function testSupportedCompatibilityTabsRemainReachable(): void {
  for (const tab of ["logs", "operations", "pipeline", "integration", "repair"]) {
    const result = resolve(`page=observability&obs_tab=${tab}`);
    assert(result.page === "observability", `${tab} should remain on the compatibility surface`);
    assert(result.params.get("obs_tab") === tab, `${tab} should remain selected`);
  }

  const invalid = resolve("page=observability&obs_tab=unknown");
  assert(invalid.page === "traces", "unknown observability tabs should fail to Traces");
}

function testProjectionLinksPersistExactlyOneEntityId(): void {
  const cases = [
    ["trace", "traces", "trace_id"],
    ["run", "runs", "run_id"],
    ["fanout", "fanouts", "fanout_id"],
    ["candidate", "candidates", "pdd_id"],
  ] as const;
  for (const [kind, page, key] of cases) {
    const result = projectionPageQuery(
      new URLSearchParams("project=p1&page=events&trace_id=old&run_id=old-run"),
      kind,
      `${kind}-1`,
    );
    assert(result.page === page, `${kind} should select ${page}`);
    assert(result.params.get(key) === `${kind}-1`, `${kind} id should be canonical`);
    assert(result.params.get("project") === "p1", `${kind} should preserve project scope`);
    const entityKeys = ["trace_id", "run_id", "fanout_id", "pdd_id"]
      .filter((item) => result.params.has(item));
    assert(entityKeys.length === 1, `${kind} should leave exactly one entity selection`);
    const selection = projectionSelectionForPage(page, result.params);
    assert(selection?.kind === kind, `${kind} should be readable from its page`);
    assert(selection?.id === `${kind}-1`, `${kind} selection should retain its id`);
  }
}

testTraceAliasesBecomeCanonicalBeforeBootstrap();
testGoalCoverageAliasKeepsSelection();
testEntityAndEvidenceAliasesKeepTheirContext();
testRetiredDiagnosticsRoutesHaveBoundedFallbacks();
testSupportedCompatibilityTabsRemainReachable();
testProjectionLinksPersistExactlyOneEntityId();

const empty = resolve("");
assert(canBootstrapScopedPageBeforeWorkspace("board", "project-a"), "a scoped Board must not wait for the workspace picker");
assert(!canBootstrapScopedPageBeforeWorkspace("board", ""), "an unscoped Board must still resolve its project");
assert(!empty.changed, "an empty URL should not be rewritten before board bootstrap");
assert(
  canBootstrapScopedPageBeforeWorkspace("traces", "project-a"),
  "an explicitly scoped Trace route should not wait for workspace discovery",
);
assert(
  !canBootstrapScopedPageBeforeWorkspace("traces", ""),
  "an unscoped Trace route still needs workspace discovery",
);
assert(
  canBootstrapScopedPageBeforeWorkspace("project", "project-a"),
  "an explicitly scoped Project dashboard should start its pulse before workspace discovery",
);
assert(
  canBootstrapScopedPageBeforeWorkspace("runs", "project-a"),
  "scoped observation routes must not wait for workspace discovery",
);
