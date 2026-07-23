import { Maximize2, Minimize2, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { getDeliveryTrace } from "../../api/client";
import type {
  DeliveryTrace,
  Feature,
  GoalCoverageGraph,
  GoalCoverageNode,
} from "../../api/types";
import { LatestRequestGate } from "../../app/latestRequestGate";
import { GoalCoverageStatus } from "./GoalCoverageStatus";
import {
  claimNodes,
  filterClaims,
  preferredClaimId,
  resultNodesByTask,
  taskNodesById,
  type GoalCoverageClaimNode,
} from "./goalCoverageModel";

export function GoalCoveragePage({
  deliveryTrace,
  embedded = false,
  features,
  onOpenWork,
  onSelectTask,
  projectId,
}: {
  deliveryTrace?: DeliveryTrace;
  embedded?: boolean;
  features: Feature[];
  onOpenWork?: (claimId: string) => void;
  onSelectTask?: (taskId: string) => void;
  projectId: string;
}) {
  const [selectedFeatureId, setSelectedFeatureId] = useState(deliveryTrace?.feature_id ?? features[0]?.id ?? "");
  const [loadedTrace, setLoadedTrace] = useState<DeliveryTrace | null>(null);
  const [selectedClaimId, setSelectedClaimId] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [focusMode, setFocusMode] = useState(false);
  const requestGateRef = useRef(new LatestRequestGate());

  useEffect(() => {
    if (features.some((feature) => feature.id === selectedFeatureId)) return;
    setSelectedFeatureId(features[0]?.id ?? "");
  }, [features, selectedFeatureId]);

  useEffect(() => {
    if (deliveryTrace) {
      requestGateRef.current.invalidate();
      setSelectedFeatureId(deliveryTrace.feature_id);
      setLoading(false);
      setError("");
      return undefined;
    }
    if (!selectedFeatureId) {
      setLoadedTrace(null);
      return undefined;
    }
    setLoadedTrace(null);
    setError("");
    let cancelled = false;
    let timer: ReturnType<typeof window.setInterval> | undefined;
    const load = (initial: boolean) => {
      if (initial) setLoading(true);
      const ticket = requestGateRef.current.issue();
      getDeliveryTrace(selectedFeatureId, projectId)
        .then((nextTrace) => {
          if (!cancelled && requestGateRef.current.isCurrent(ticket)) {
            setLoadedTrace(nextTrace);
            setError("");
            setLoading(false);
          }
        })
        .catch((reason) => {
          if (!cancelled && requestGateRef.current.isCurrent(ticket)) {
            setError(String(reason?.message ?? reason));
            setLoading(false);
          }
        });
    };
    load(true);
    timer = window.setInterval(() => load(false), 8000);
    return () => {
      cancelled = true;
      requestGateRef.current.invalidate();
      if (timer) window.clearInterval(timer);
    };
  }, [deliveryTrace, projectId, selectedFeatureId]);

  useEffect(() => {
    if (!focusMode) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFocusMode(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [focusMode]);

  const trace = deliveryTrace ?? loadedTrace;
  const graph = trace?.goal_coverage_graph ?? null;
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
  const selectedFeature = features.find((feature) => feature.id === selectedFeatureId) ?? null;
  const projectionError = graph?.diagnostics.find((item) => item.code === "projection_error");

  return (
    <div
      className={`goal-coverage-page ${embedded ? "is-embedded" : ""} ${focusMode ? "is-focus" : ""}`}
      data-testid="goal-coverage-page"
    >
      <header className="goal-coverage-toolbar">
        {!embedded ? (
          <div className="goal-coverage-title">
            <h2>Goal Coverage</h2>
            <span className="muted">Plan · Execution · Verification · Closure</span>
          </div>
        ) : null}
        <div className="goal-coverage-controls">
          {!embedded ? (
            <label>
              <span>Delivery</span>
              <select
                aria-label="Delivery"
                value={selectedFeatureId}
                onChange={(event) => {
                  setSelectedFeatureId(event.target.value);
                  setSelectedClaimId("");
                  setQuery("");
                }}
              >
                {features.map((feature) => (
                  <option key={feature.id} value={feature.id}>
                    {feature.title || feature.id}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <div className="goal-coverage-generation" aria-label="Current generation">
            <span>Generation</span>
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

      {features.length === 0 ? (
        <GoalCoverageEmpty title="No delivery goals" detail="Goal coverage appears with an accepted task map." />
      ) : loading && !graph ? (
        <GoalCoverageEmpty title="Loading goal coverage" detail={selectedFeature?.title || selectedFeatureId} />
      ) : error && !graph ? (
        <GoalCoverageEmpty title="Goal coverage unavailable" detail={error} tone="err" />
      ) : graph ? (
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
          <div className="goal-coverage-workbench">
            <section className="goal-coverage-canvas" aria-label="Goal coverage graph">
              <GoalNode graph={graph} goal={goal} />
              <div className="goal-coverage-column-head" aria-hidden="true">
                <span>Claim</span>
                <span>Plan</span>
                <span>Implementation</span>
                <span>Verification</span>
                <span>Closure</span>
              </div>
              <div className="goal-coverage-rows">
                {filteredClaims.map((claim) => (
                  <ClaimRow
                    claim={claim}
                    key={claim.goal_claim_id}
                    onOpenWork={onOpenWork}
                    onSelect={() => setSelectedClaimId(claim.goal_claim_id)}
                    selected={claim.goal_claim_id === selectedClaim?.goal_claim_id}
                    taskNodes={tasks}
                  />
                ))}
                {filteredClaims.length === 0 ? (
                  <div className="goal-coverage-no-results">No matching claims</div>
                ) : null}
              </div>
            </section>
            <ClaimInspector
              claim={selectedClaim}
              onOpenWork={onOpenWork}
              onSelectTask={onSelectTask}
              resultNodes={results}
              taskNodes={tasks}
            />
          </div>
        </>
      ) : null}
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
  return (
    <div className="goal-coverage-goal-node" data-testid="goal-coverage-goal-node">
      <span className="goal-coverage-kicker">Goal</span>
      <strong>{goal?.title || graph.identity.goal_id || "Goal"}</strong>
      <span className="mono">{graph.identity.goal_id || "-"}</span>
      <span className="goal-coverage-goal-meta">
        Run {compactIdentity(graph.identity.workflow_run_id)} · Gen {shortGeneration(graph.identity.task_map_generation)}
      </span>
    </div>
  );
}

function ClaimRow({
  claim,
  onOpenWork,
  onSelect,
  selected,
  taskNodes,
}: {
  claim: GoalCoverageClaimNode;
  onOpenWork?: (claimId: string) => void;
  onSelect: () => void;
  selected: boolean;
  taskNodes: Map<string, GoalCoverageNode>;
}) {
  const claimTasks = (claim.task_ids ?? []).map((taskId) => taskNodes.get(taskId)).filter(Boolean) as GoalCoverageNode[];
  const doneTasks = claimTasks.filter((task) => isDoneStatus(task.status)).length;
  const taskLabel = claimTasks.length === 1 ? "1 task" : `${claimTasks.length} tasks`;
  return (
    <article
      className={`goal-coverage-row ${selected ? "selected" : ""}`}
      data-claim-id={claim.goal_claim_id}
      data-testid="goal-coverage-claim-row"
    >
      <button className="goal-coverage-claim-node" type="button" onClick={onSelect}>
        <span className="goal-coverage-mobile-label">Claim</span>
        <span className="goal-coverage-node-head">
          <strong>{claim.title}</strong>
          <span>{claim.mandatory === false ? "optional" : "mandatory"}</span>
        </span>
        <span className="mono">{claim.goal_claim_id}</span>
      </button>
      <div className="goal-coverage-cell goal-coverage-matrix-cell goal-coverage-plan-cell">
        <span className="goal-coverage-mobile-label">Plan</span>
        <GoalCoverageStatus label={claim.plan_coverage ?? "uncovered"} status={claim.plan_coverage} />
        <span className={claimTasks.length ? "muted" : "goal-coverage-missing"}>
          {claimTasks.length ? taskLabel : "No owner"}
        </span>
        {onOpenWork ? (
          <button
            className="goal-coverage-open-work"
            onClick={() => onOpenWork(claim.goal_claim_id)}
            type="button"
          >
            Open in Work
          </button>
        ) : null}
      </div>
      <div className="goal-coverage-cell goal-coverage-matrix-cell goal-coverage-implementation-cell">
        <span className="goal-coverage-mobile-label">Implementation</span>
        <GoalCoverageStatus label={claim.execution ?? "pending"} status={claim.execution} />
        <span className="muted">
          {claimTasks.length ? `${doneTasks}/${claimTasks.length} done` : "Not planned"}
        </span>
      </div>
      <div className="goal-coverage-cell goal-coverage-matrix-cell goal-coverage-verification-cell">
        <span className="goal-coverage-mobile-label">Verification</span>
        <GoalCoverageStatus label={claim.task_verification ?? "unverified"} status={claim.task_verification} />
      </div>
      <div className="goal-coverage-cell goal-coverage-matrix-cell goal-coverage-closure-cell">
        <span className="goal-coverage-mobile-label">Closure</span>
        <GoalCoverageStatus label={claim.closure ?? "unknown"} status={claim.closure} />
        {(claim.gap_refs?.length ?? 0) > 0 ? (
          <span className="goal-coverage-gap-count">{claim.gap_refs!.length} gap</span>
        ) : null}
      </div>
    </article>
  );
}

function ClaimInspector({
  claim,
  onOpenWork,
  onSelectTask,
  resultNodes,
  taskNodes,
}: {
  claim: GoalCoverageClaimNode | null;
  onOpenWork?: (claimId: string) => void;
  onSelectTask?: (taskId: string) => void;
  resultNodes: Map<string, GoalCoverageNode[]>;
  taskNodes: Map<string, GoalCoverageNode>;
}) {
  if (!claim) {
    return <aside className="goal-coverage-inspector"><span className="muted">No claim selected</span></aside>;
  }
  const tasks = (claim.task_ids ?? []).map((taskId) => taskNodes.get(taskId)).filter(Boolean) as GoalCoverageNode[];
  const results = tasks.flatMap((task) => resultNodes.get(task.task_id ?? "") ?? []);
  const resultRefs = Array.from(new Set([
    ...results.map((result) => result.result_ref).filter(Boolean) as string[],
    ...(claim.supporting_result_refs ?? []),
  ]));
  return (
    <aside className="goal-coverage-inspector" aria-label="Claim inspector" data-testid="goal-coverage-inspector">
      <div className="goal-coverage-inspector-head">
        <span className="goal-coverage-kicker">Claim</span>
        <h3>{claim.title}</h3>
        <span className="mono">{claim.goal_claim_id}</span>
      </div>
      <dl className="goal-coverage-axes">
        <Axis label="Plan coverage" value={claim.plan_coverage ?? "uncovered"} />
        <Axis label="Execution" value={claim.execution ?? "pending"} />
        <Axis label="Task verification" value={claim.task_verification ?? "unverified"} />
        <Axis label="Goal closure" value={claim.closure ?? "unknown"} />
      </dl>
      {onOpenWork ? (
        <button
          className="goal-coverage-inspector-action"
          onClick={() => onOpenWork(claim.goal_claim_id)}
          type="button"
        >
          Open in Work
        </button>
      ) : null}
      <InspectorSection title="Source">
        <span className="mono goal-coverage-long-value">{claim.source_ref || "not recorded"}</span>
        <span>{claim.mandatory === false ? "Optional" : "Mandatory"}</span>
      </InspectorSection>
      <InspectorSection title="Tasks">
        {tasks.length ? tasks.map((task) => (
          <button
            className="goal-coverage-inspector-row"
            disabled={!task.task_id || !onSelectTask}
            key={task.node_id}
            onClick={() => task.task_id && onSelectTask?.(task.task_id)}
            type="button"
          >
            <span>{task.title}</span>
            <span className="mono">{task.task_id}</span>
          </button>
        )) : <span className="goal-coverage-missing">No covering task</span>}
      </InspectorSection>
      <InspectorSection title="Result refs">
        {resultRefs.length ? resultRefs.map((resultRef) => (
          <span className="mono goal-coverage-long-value" key={resultRef}>
            {resultRef}
          </span>
        )) : <span className="muted">No current result</span>}
      </InspectorSection>
      {(claim.gap_refs?.length ?? 0) > 0 ? (
        <InspectorSection title="Open gaps">
          {claim.gap_refs!.map((ref) => (
            <span className="mono goal-coverage-long-value" key={ref}>{ref}</span>
          ))}
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
