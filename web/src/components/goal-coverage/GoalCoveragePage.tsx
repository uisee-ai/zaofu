import { useSettingsPreferences } from "../../settings/useSettingsPreferences";
import { uiText } from "../../settings/uiText";
import { Maximize2, Minimize2, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type {
  DeliveryTrace,
  GoalCoverageGraph,
  GoalCoverageNode,
} from "../../api/types";
import { GoalCoverageStatus } from "./GoalCoverageStatus";
import {
  claimNodes,
  filterClaims,
  hasUnavailableTaskDetails,
  missingTaskDetailCount,
  preferredClaimId,
  resultNodesByTask,
  taskNodesById,
  type GoalCoverageClaimNode,
} from "./goalCoverageModel";

export function GoalCoveragePage({
  deliveryTrace,
  onSelectTask,
}: {
  deliveryTrace: DeliveryTrace;
  onSelectTask?: (taskId: string) => void;
}) {
  useSettingsPreferences();
  const [selectedClaimId, setSelectedClaimId] = useState("");
  const [query, setQuery] = useState("");
  const [focusMode, setFocusMode] = useState(false);

  useEffect(() => {
    if (!focusMode) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFocusMode(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [focusMode]);

  const trace = deliveryTrace;
  const graph = trace.goal_coverage_graph ?? null;
  const claims = useMemo(() => claimNodes(graph), [graph]);
  const filteredClaims = useMemo(() => filterClaims(graph, query), [graph, query]);
  const tasks = useMemo(() => taskNodesById(graph), [graph]);
  const results = useMemo(() => resultNodesByTask(graph), [graph]);

  useEffect(() => {
    setSelectedClaimId((current) => preferredClaimId(graph, current));
  }, [graph]);

  useEffect(() => {
    if (!query.trim() || filteredClaims.length === 0) return;
    if (!filteredClaims.some((claim) => claim.goal_claim_id === selectedClaimId)) {
      setSelectedClaimId(filteredClaims[0]!.goal_claim_id);
    }
  }, [filteredClaims, query, selectedClaimId]);

  const selectedClaim = claims.find((claim) => claim.goal_claim_id === selectedClaimId)
    ?? filteredClaims[0]
    ?? claims[0]
    ?? null;
  const goal = graph?.nodes.find((node) => node.kind === "goal") ?? null;
  const projectionError = graph?.diagnostics.find((item) => item.code === "projection_error");
  const relationDetailsTruncated = Boolean(graph?.nodes.some((node) => (
    node.task_ids_truncated
      || node.task_details?.task_ids_truncated
      || node.task_details?.missing_task_ids_truncated
      || node.goal_claim_ids_truncated
      || node.supporting_result_refs_truncated
      || node.evidence_refs_truncated
      || node.gap_refs_truncated
      || node.stale_reasons_truncated
  )));

  return (
    <div
      className={`goal-coverage-page is-embedded ${focusMode ? "is-focus" : ""}`}
      data-testid="goal-coverage-page"
    >
      <header className="goal-coverage-toolbar">
        <div className="goal-coverage-controls">
          <div className="goal-coverage-generation" aria-label="Current generation">
            <span>{uiText("Generation")}</span>
            <strong className="mono">{shortGeneration(graph?.identity.task_map_generation)}</strong>
            <small>current</small>
          </div>
          <label className="goal-coverage-search">
            <Search aria-hidden="true" size={15} />
            <input
              aria-label="Search claims and tasks"
              placeholder="Search claims or tasks"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <button
            aria-label={focusMode ? "Exit focus mode" : "Enter focus mode"}
            className="icon-button goal-coverage-focus"
            title={focusMode ? "Exit focus mode" : "Enter focus mode"}
            type="button"
            onClick={() => setFocusMode((current) => !current)}
          >
            {focusMode
              ? <Minimize2 aria-hidden="true" size={17} />
              : <Maximize2 aria-hidden="true" size={17} />}
          </button>
        </div>
      </header>

      {graph ? (
        <>
          <CoverageSummary graph={graph} />
          {projectionError ? (
            <div className="goal-coverage-notice tone-error" data-testid="goal-coverage-mode-notice">
              <GoalCoverageStatus label="projection error" status="failed" />
              <span>{projectionError.message || "Goal coverage could not be projected."}</span>
            </div>
          ) : graph.coverage_mode !== "explicit" ? (
            <div className="goal-coverage-notice" data-testid="goal-coverage-mode-notice">
              <GoalCoverageStatus label={graph.coverage_mode} status="stale" />
              <span>
                {graph.coverage_mode === "legacy_derived"
                  ? "Claim links are derived from task acceptance."
                  : "No Goal Claim mapping is available for this delivery."}
              </span>
            </div>
          ) : null}
          {graph.nodes_truncated || graph.edges_truncated || graph.diagnostics_truncated || relationDetailsTruncated ? (
            <div className="goal-coverage-notice" data-testid="goal-coverage-truncation-notice">
              <GoalCoverageStatus label="bounded view" status="stale" />
              <span>
                Some coverage details were omitted by projection limits
                {graph.nodes_truncated ? "; task owner details may be unavailable" : ""}
                {relationDetailsTruncated ? "; some relation refs are shown partially" : ""}.
              </span>
            </div>
          ) : null}
          <div className="goal-coverage-workbench">
            <section className="goal-coverage-canvas" aria-label="Goal coverage graph">
              <GoalNode graph={graph} goal={goal} />
              <div className="goal-coverage-column-head" aria-hidden="true">
                <span>{uiText("Claim")}</span>
                <span>{uiText("Plan")}</span>
                <span>Implementation</span>
                <span>{uiText("Verification")}</span>
                <span>{uiText("Closure")}</span>
              </div>
              <div className="goal-coverage-rows">
                {filteredClaims.map((claim) => (
                  <ClaimRow
                    claim={claim}
                    key={claim.goal_claim_id}
                    onSelect={() => setSelectedClaimId(claim.goal_claim_id)}
                    selected={claim.goal_claim_id === selectedClaim?.goal_claim_id}
                    taskNodes={tasks}
                    taskDetailsUnavailable={hasUnavailableTaskDetails(graph, claim, tasks)}
                    taskDetailsMissingCount={missingTaskDetailCount(graph, claim, tasks)}
                  />
                ))}
                {filteredClaims.length === 0 ? (
                  <div className="goal-coverage-no-results">{uiText("No matching claims")}</div>
                ) : null}
              </div>
            </section>
            <ClaimInspector
              claim={selectedClaim}
              onSelectTask={onSelectTask}
              resultNodes={results}
              taskNodes={tasks}
              taskDetailsUnavailable={selectedClaim ? hasUnavailableTaskDetails(graph, selectedClaim, tasks) : false}
              taskDetailsMissingCount={selectedClaim ? missingTaskDetailCount(graph, selectedClaim, tasks) : 0}
            />
          </div>
        </>
      ) : (
        <GoalCoverageEmpty title="Goal coverage unavailable" detail="No Goal → Claim → Task projection is available for this delivery." />
      )}
    </div>
  );
}

function CoverageSummary({ graph }: { graph: GoalCoverageGraph }) {
  const metrics = [
    ["Planned", `${graph.summary.planned_claims}/${graph.summary.mandatory_claims}`],
    ["Current results", graph.summary.claims_with_current_results],
    ["Closed", graph.summary.closed_claims],
    ["Open gaps", graph.summary.open_gaps],
  ];
  return (
    <div className="goal-coverage-summary" data-testid="goal-coverage-summary">
      {metrics.map(([label, value]) => (
        <span key={label}>
          <small>{label}</small>
          <strong>{value}</strong>
        </span>
      ))}
      <span className="goal-coverage-summary-spacer" />
      <GoalCoverageStatus
        label={graph.currentness.is_current_generation ? "current" : "superseded"}
        status={graph.currentness.is_current_generation ? "passed" : "stale"}
      />
    </div>
  );
}

function GoalNode({ graph, goal }: { graph: GoalCoverageGraph; goal: GoalCoverageNode | null }) {
  useSettingsPreferences();
  return (
    <div className="goal-coverage-goal-node" data-testid="goal-coverage-goal-node">
      <span className="goal-coverage-kicker">{uiText("Goal")}</span>
      <strong>{goal?.title || graph.identity.goal_id || "Goal"}</strong>
      <span className="mono">{graph.identity.goal_id || "-"}</span>
      <span className="goal-coverage-goal-meta">
        {uiText("Run")} {compactIdentity(graph.identity.workflow_run_id)} · Gen {shortGeneration(graph.identity.task_map_generation)}
      </span>
    </div>
  );
}

function ClaimRow({
  claim,
  onSelect,
  selected,
  taskDetailsUnavailable,
  taskDetailsMissingCount,
  taskNodes,
}: {
  claim: GoalCoverageClaimNode;
  onSelect: () => void;
  selected: boolean;
  taskDetailsUnavailable: boolean;
  taskDetailsMissingCount: number;
  taskNodes: Map<string, GoalCoverageNode>;
}) {
  useSettingsPreferences();
  const claimTasks = (claim.task_ids ?? []).map((taskId) => taskNodes.get(taskId)).filter(Boolean) as GoalCoverageNode[];
  const doneTasks = claimTasks.filter((task) => isDoneStatus(task.status)).length;
  const taskTotal = Math.max(claimTasks.length, claim.task_details?.total ?? claim.task_ids?.length ?? 0);
  const taskLabel = taskTotal === 1 ? "1 task" : `${taskTotal} tasks`;
  const gapTotal = Math.max(claim.gap_refs?.length ?? 0, claim.gap_refs_total ?? 0);
  const gapsShown = claim.gap_refs?.length ?? 0;
  return (
    <article
      className={`goal-coverage-row ${selected ? "selected" : ""}`}
      data-claim-id={claim.goal_claim_id}
      data-testid="goal-coverage-claim-row"
    >
      <button className="goal-coverage-claim-node" type="button" onClick={onSelect}>
        <span className="goal-coverage-mobile-label">{uiText("Claim")}</span>
        <span className="goal-coverage-node-head">
          <strong>{claim.title}</strong>
          <span>{claim.mandatory === false ? "optional" : "mandatory"}</span>
        </span>
        <span className="mono">{claim.goal_claim_id}</span>
      </button>
      <div className="goal-coverage-cell goal-coverage-matrix-cell goal-coverage-plan-cell">
        <span className="goal-coverage-mobile-label">{uiText("Plan")}</span>
        <GoalCoverageStatus label={claim.plan_coverage ?? "uncovered"} status={claim.plan_coverage} />
        <span className={claimTasks.length || taskDetailsUnavailable ? "muted" : "goal-coverage-missing"}>
          {taskDetailsUnavailable
            ? `Owner details unavailable (${taskDetailsMissingCount} omitted)`
            : taskTotal ? taskLabel : "No owner"}
        </span>
      </div>
      <div className="goal-coverage-cell goal-coverage-matrix-cell goal-coverage-implementation-cell">
        <span className="goal-coverage-mobile-label">Implementation</span>
        <GoalCoverageStatus label={claim.execution ?? "pending"} status={claim.execution} />
        <span className="muted">
          {taskDetailsUnavailable
            ? `${doneTasks}/${taskTotal} done · details partial`
            : taskTotal ? `${doneTasks}/${taskTotal} done` : "Not planned"}
        </span>
      </div>
      <div className="goal-coverage-cell goal-coverage-matrix-cell goal-coverage-verification-cell">
        <span className="goal-coverage-mobile-label">{uiText("Verification")}</span>
        <GoalCoverageStatus label={claim.task_verification ?? "unverified"} status={claim.task_verification} />
      </div>
      <div className="goal-coverage-cell goal-coverage-matrix-cell goal-coverage-closure-cell">
        <span className="goal-coverage-mobile-label">{uiText("Closure")}</span>
        <GoalCoverageStatus label={claim.closure ?? "unknown"} status={claim.closure} />
        {gapTotal > 0 ? (
          <span className="goal-coverage-gap-count">
            {gapTotal} gap{gapTotal === 1 ? "" : "s"}
            {claim.gap_refs_truncated ? ` · ${gapsShown} shown` : ""}
          </span>
        ) : null}
      </div>
    </article>
  );
}

function ClaimInspector({
  claim,
  onSelectTask,
  resultNodes,
  taskDetailsUnavailable,
  taskDetailsMissingCount,
  taskNodes,
}: {
  claim: GoalCoverageClaimNode | null;
  onSelectTask?: (taskId: string) => void;
  resultNodes: Map<string, GoalCoverageNode[]>;
  taskDetailsUnavailable: boolean;
  taskDetailsMissingCount: number;
  taskNodes: Map<string, GoalCoverageNode>;
}) {
  useSettingsPreferences();
  if (!claim) {
    return <aside className="goal-coverage-inspector"><span className="muted">{uiText("No claim selected")}</span></aside>;
  }
  const tasks = (claim.task_ids ?? []).map((taskId) => taskNodes.get(taskId)).filter(Boolean) as GoalCoverageNode[];
  const results = tasks.flatMap((task) => resultNodes.get(task.task_id ?? "") ?? []);
  const resultRefs = Array.from(new Set([
    ...results.map((result) => result.result_ref).filter(Boolean) as string[],
    ...(claim.supporting_result_refs ?? []),
  ]));
  const gapRefs = claim.gap_refs ?? [];
  const gapRefsOmitted = Math.max(
    0,
    claim.gap_refs_omitted
      ?? (claim.gap_refs_total ?? gapRefs.length) - gapRefs.length,
  );
  const resultRefsOmitted = Math.max(
    0,
    claim.supporting_result_refs_omitted
      ?? (claim.supporting_result_refs_total ?? claim.supporting_result_refs?.length ?? 0)
        - (claim.supporting_result_refs?.length ?? 0),
  );
  return (
    <aside className="goal-coverage-inspector" aria-label="Claim inspector" data-testid="goal-coverage-inspector">
      <div className="goal-coverage-inspector-head">
        <span className="goal-coverage-kicker">{uiText("Claim")}</span>
        <h3>{claim.title}</h3>
        <span className="mono">{claim.goal_claim_id}</span>
      </div>
      <dl className="goal-coverage-axes">
        <Axis label="Plan coverage" value={claim.plan_coverage ?? "uncovered"} />
        <Axis label={uiText("Execution")} value={claim.execution ?? "pending"} />
        <Axis label="Task verification" value={claim.task_verification ?? "unverified"} />
        <Axis label="Goal closure" value={claim.closure ?? "unknown"} />
      </dl>
      <InspectorSection title={uiText("Source")}>
        <span className="mono goal-coverage-long-value">{claim.source_ref || "not recorded"}</span>
        <span>{claim.mandatory === false ? "Optional" : "Mandatory"}</span>
      </InspectorSection>
      <InspectorSection title={uiText("Tasks")}>
        {tasks.map((task) => (
          <button
            className="goal-coverage-inspector-row"
            disabled={!task.task_id || task.task_id_opaque || !onSelectTask}
            key={task.node_id}
            onClick={() => {
              if (task.task_id && !task.task_id_opaque) onSelectTask?.(task.task_id);
            }}
            type="button"
          >
            <span>{task.title}</span>
            <span className="mono">{task.task_id}</span>
          </button>
        ))}
        {tasks.some((task) => task.task_id_opaque) ? (
          <span className="muted" data-testid="goal-coverage-task-id-opaque">
            A canonical task ID was omitted from this bounded view; open the task from Tasks.
          </span>
        ) : null}
        {taskDetailsUnavailable ? (
          <span className="muted" data-testid="goal-coverage-task-details-truncated">
            {taskDetailsMissingCount} task owner detail{taskDetailsMissingCount === 1 ? "" : "s"} omitted by the bounded projection.
          </span>
        ) : tasks.length === 0 ? <span className="goal-coverage-missing">{uiText("No covering task")}</span> : null}
      </InspectorSection>
      <InspectorSection title="Result refs">
        {resultRefs.length ? resultRefs.map((resultRef) => (
          <span className="mono goal-coverage-long-value" key={resultRef}>
            {resultRef}
          </span>
        )) : <span className="muted">{uiText("No current result")}</span>}
        {claim.supporting_result_refs_truncated ? (
          <span className="muted" data-testid="goal-coverage-result-refs-truncated">
            {resultRefsOmitted} supporting result ref{resultRefsOmitted === 1 ? "" : "s"} omitted by the bounded projection.
          </span>
        ) : null}
      </InspectorSection>
      {gapRefs.length > 0 || (claim.gap_refs_total ?? 0) > 0 ? (
        <InspectorSection title="Open gaps">
          {gapRefs.map((ref) => (
            <span className="mono goal-coverage-long-value" key={ref}>{ref}</span>
          ))}
          {claim.gap_refs_truncated ? (
            <span className="muted" data-testid="goal-coverage-gap-refs-truncated">
              {gapRefsOmitted} gap ref{gapRefsOmitted === 1 ? "" : "s"} omitted by the bounded projection.
            </span>
          ) : null}
        </InspectorSection>
      ) : null}
    </aside>
  );
}

function InspectorSection({ children, title }: { children: React.ReactNode; title: string }) {
  return (
    <section className="goal-coverage-inspector-section">
      <h4>{title}</h4>
      <div>{children}</div>
    </section>
  );
}

function Axis({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd><GoalCoverageStatus label={value} status={value} /></dd>
    </div>
  );
}

function GoalCoverageEmpty({
  detail,
  title,
  tone = "muted",
}: {
  detail: string;
  title: string;
  tone?: "muted" | "err";
}) {
  return (
    <div className={`goal-coverage-empty tone-${tone}`}>
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function shortGeneration(value: string | undefined): string {
  if (!value) return "current";
  return value.length > 12 ? value.slice(0, 12) : value;
}

function compactIdentity(value: string | undefined): string {
  if (!value) return "-";
  if (value.length <= 28) return value;
  return `${value.slice(0, 12)}…${value.slice(-10)}`;
}

function isDoneStatus(status: string | undefined): boolean {
  return ["done", "completed", "passed", "shipped", "cancelled"].includes(status ?? "");
}
