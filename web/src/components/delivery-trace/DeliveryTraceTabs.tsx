import { useSettingsPreferences } from "../../settings/useSettingsPreferences";
import { uiText } from "../../settings/uiText";
// Delivery Runs workbench. Run answers current state and causation; task
// attempts and regression actions stay in the selected task's drawer.
import { Fragment, useEffect, useState } from "react";

import type {
  DeliveryRunGroup,
  DeliveryTaskFlowStage,
  DeliveryTrace,
  DeliveryWorkflowStageRun,
} from "../../api/types";
import type { PageId } from "../../app/sharedTypes";
import { dtTone, formatDuration } from "./DeliveryTraceViewUtils";
import { LifecycleDrawer } from "./LifecycleDrawer";
import type { LifecycleDrawerTab } from "./LifecycleDrawer";
import { RunGraphView } from "./RunGraphView";
import type { CausalState } from "./RunGraphView";

interface DrawerTarget {
  taskId: string;
  tab?: LifecycleDrawerTab;
  trySel?: number;
}

interface DeliveryTraceTabsProps {
  onOpenPage?: (page: PageId) => void;
  projectId?: string;
  trace: DeliveryTrace;
}

export function DeliveryTraceTabs({ onOpenPage, projectId, trace }: DeliveryTraceTabsProps) {
  useSettingsPreferences();
  const [selectedStageId, setSelectedStageId] = useState("");
  const [drawer, setDrawer] = useState<DrawerTarget | null>(null);
  const [causal, setCausal] = useState<CausalState | null>(null);
  const stages = trace.task_flow?.stages ?? [];
  // S-E: run-chain.v1 drives the Run Graph; absent/no_stage_order falls back
  // to the legacy stage-line Flow rendering (kept below, not deleted).
  const runChain = trace.run_chain;
  const hasRunGraph = !!runChain && runChain.status !== "no_stage_order" && runChain.stages.length > 0;

  useEffect(() => {
    setDrawer(null);
    setCausal(null);
  }, [trace.feature_id]);

  useEffect(() => {
    const candidate =
      stages.find((stage) => trace.task_flow?.active_stage_ids.includes(stage.stage_id))
      ?? stages[0];
    if (candidate && !stages.some((stage) => stage.stage_id === selectedStageId)) {
      setSelectedStageId(candidate.stage_id);
    }
  }, [selectedStageId, stages, trace.task_flow?.active_stage_ids]);

  const stageCount = hasRunGraph ? runChain!.stages.length : stages.length;

  return (
    <section className="delivery-tabbed-workbench" data-testid="delivery-tabs">
      <div className="delivery-run-surface-head" data-testid="delivery-run-surface-head">
        <strong>{uiText("Run")}</strong>
        <span className="muted">{stageCount} stage{stageCount === 1 ? "" : "s"} · select a task for attempts and evidence</span>
      </div>
      {hasRunGraph ? (
        <RunGraphView
          causal={causal}
          onCausalChange={setCausal}
          onSelectTask={(taskId) => setDrawer({ taskId })}
          projectId={projectId}
          trace={trace}
        />
      ) : (
        <DeliveryFlowTab
          selectedStageId={selectedStageId}
          setSelectedStageId={setSelectedStageId}
          trace={trace}
        />
      )}
      {drawer && (
        <LifecycleDrawer
          key={`${drawer.taskId}:${drawer.tab ?? ""}:${drawer.trySel ?? ""}`}
          initialTab={drawer.tab}
          initialTry={drawer.trySel}
          onClose={() => setDrawer(null)}
          onOpenPage={onOpenPage}
          projectId={projectId}
          taskId={drawer.taskId}
          trace={trace}
        />
      )}
    </section>
  );
}

function DeliveryFlowTab({
  selectedStageId,
  setSelectedStageId,
  trace,
}: {
  selectedStageId: string;
  setSelectedStageId: (id: string) => void;
  trace: DeliveryTrace;
}) {
  useSettingsPreferences();
  const stages = trace.task_flow?.stages ?? [];
  const runGroups = trace.run_groups ?? [];
  const selected = stages.find((stage) => stage.stage_id === selectedStageId) ?? stages[0];
  if (!stages.length) {
    return (
      <div className="delivery-tab-empty">
        <strong>{uiText("No task-flow projection.")}</strong>
        <span className="muted">Run is limited to the available attempt and timeline evidence.</span>
      </div>
    );
  }
  return (
    <div className="delivery-flow-workbench" data-testid="delivery-flow-tab">
      <StageLine
        runGroups={runGroups}
        selectedStageId={selected?.stage_id ?? ""}
        setSelectedStageId={setSelectedStageId}
        stages={stages}
      />
      <div className="delivery-stage-detail-grid">
        {selected ? (
          <StageWorkPanel runGroups={runGroupsForStage(selected, runGroups)} stage={selected} />
        ) : (
          <section className="delivery-flow-stage-panel">
            <p className="muted">{uiText("Select a stage.")}</p>
          </section>
        )}
        <StageInspector
          runGroups={selected ? runGroupsForStage(selected, runGroups) : []}
          stage={selected}
          trace={trace}
        />
      </div>
    </div>
  );
}

function StageLine({
  runGroups,
  selectedStageId,
  setSelectedStageId,
  stages,
}: {
  runGroups: DeliveryRunGroup[];
  selectedStageId: string;
  setSelectedStageId: (id: string) => void;
  stages: DeliveryTaskFlowStage[];
}) {
  return (
    <section className="delivery-stage-line-panel" aria-label="Delivery stage line">
      <div className="delivery-stage-line">
        {stages.map((stage) => (
          <button
            key={stage.stage_id}
            type="button"
            className={`delivery-stage-node ${stage.stage_id === selectedStageId ? "active" : ""}`}
            onClick={() => setSelectedStageId(stage.stage_id)}
          >
            <span className={`delivery-stage-point status-${dtTone(stage.status)}`} />
            <strong>{stageDisplayLabel(stage)}</strong>
            <small>{stageSummary(stage, runGroupsForStage(stage, runGroups))}</small>
          </button>
        ))}
      </div>
    </section>
  );
}

function StageWorkPanel({
  runGroups,
  stage,
}: {
  runGroups: DeliveryRunGroup[];
  stage: DeliveryTaskFlowStage;
}) {
  useSettingsPreferences();
  const [expandedRunId, setExpandedRunId] = useState("");
  useEffect(() => {
    const candidate =
      runGroups.find((run) => ["failed", "running", "blocked"].includes(run.status))
      ?? runGroups[0];
    if (candidate && !runGroups.some((run) => run.group_id === expandedRunId)) {
      setExpandedRunId(candidate.group_id);
    }
  }, [expandedRunId, runGroups]);
  const expandedRun = runGroups.find((run) => run.group_id === expandedRunId) ?? runGroups[0];
  const hasFanout = runGroups.some((run) => run.children.length > 0 || run.kind === "fanout");
  return (
    <section className="delivery-flow-stage-panel">
      <div className="delivery-stage-panel-head">
        <div>
          <span className="eyebrow">{uiText("Selected Stage")}</span>
          <h3>{stageDisplayLabel(stage)}</h3>
        </div>
        <div className="delivery-stage-panel-badges">
          <span className={`badge badge-${dtTone(stage.status)}`}>{stage.status}</span>
          <span className="badge">{hasFanout ? "fanout" : "stage"}</span>
        </div>
      </div>
      {runGroups.length ? (
        <div className="delivery-stage-run-groups">
          <div className="delivery-stage-run-selector">
            {runGroups.map((run) => (
              <button
                key={run.group_id}
                type="button"
                className={`delivery-stage-run-chip ${run.group_id === expandedRun?.group_id ? "active" : ""}`}
                onClick={() => setExpandedRunId(run.group_id)}
              >
                <span className={`workflow-status-dot status-${dtTone(run.status)}`} />
                <span>{run.label || run.group_id}</span>
                <small>{run.children.length || run.task_ids.length} lanes</small>
              </button>
            ))}
          </div>
          {expandedRun ? <FanoutDag run={expandedRun} /> : null}
        </div>
      ) : (
        <StageTaskList stage={stage} />
      )}
    </section>
  );
}

function StageTaskList({ stage }: { stage: DeliveryTaskFlowStage }) {
  useSettingsPreferences();
  if (!stage.tasks.length) {
    return (
      <div className="delivery-stage-empty-lane">
        <span className={`workflow-status-dot status-${dtTone(stage.status)}`} />
        <div>
          <strong>{stage.label || stage.stage_id}</strong>
          <p className="muted">{uiText("No tasks currently mapped to this stage.")}</p>
        </div>
        <span className={`badge badge-${dtTone(stage.status)}`}>{stage.status}</span>
      </div>
    );
  }
  return (
    <div className="delivery-flow-task-list">
      {stage.tasks.map((task) => (
        <article key={task.task_id} className="delivery-flow-task-row" data-testid="delivery-flow-task">
          <div>
            <strong>{task.title || task.task_id}</strong>
            <small className="mono">{task.task_id}</small>
          </div>
          <span className={`badge badge-${dtTone(task.status)}`}>{task.status}</span>
          <span>{task.owner_role || task.assigned_to || "-"}</span>
          <span>{task.latest_event?.event_type || "no event"}</span>
        </article>
      ))}
    </div>
  );
}

function FanoutDag({ run }: { run: DeliveryRunGroup }) {
  useSettingsPreferences();
  const children = run.children.slice(0, 12);
  return (
    <div className="delivery-fanout-dag" data-testid="delivery-fanout-dag">
      <div className="delivery-fanout-dag-head">
        <div>
          <strong>{run.label || run.group_id}</strong>
          <small>{run.kind} / {run.operator_kind || "stage"} · {formatDuration(run.duration_ms)}</small>
        </div>
        <span className={`badge badge-${dtTone(run.status)}`}>{run.status}</span>
      </div>
      <div className="delivery-fanout-lanes">
        {children.map((child, index) => (
          <div key={`${String(child.child_id ?? child.run_id ?? index)}`} className="delivery-fanout-lane">
            <span className={`workflow-status-dot status-${dtTone(String(child.status ?? ""))}`} />
            <div>
              <strong>{String(child.child_id ?? child.run_id ?? `lane-${index + 1}`)}</strong>
              <small>{String(child.backend ?? child.worker_id ?? child.role ?? child.role_instance ?? "agent")}</small>
            </div>
            <span className={`badge badge-${dtTone(String(child.status ?? ""))}`}>{String(child.status ?? "-")}</span>
          </div>
        ))}
        {!children.length && (
          <div className="delivery-stage-empty-lane">
            <span className={`workflow-status-dot status-${dtTone(run.status)}`} />
            <div>
              <strong>{uiText("No child lanes projected")}</strong>
              <p className="muted">This run group has source events but no fanout children.</p>
            </div>
            <span className={`badge badge-${dtTone(run.status)}`}>{run.status}</span>
          </div>
        )}
      </div>
      <div className="delivery-fanout-aggregate">
        <span className={`workflow-status-dot status-${dtTone(run.status)}`} />
        <div>
          <strong>aggregate</strong>
          <small>{run.task_ids.length} tasks · {run.source_event_ids?.length ?? 0} events</small>
        </div>
        <span className={`badge badge-${dtTone(run.status)}`}>{run.status}</span>
      </div>
    </div>
  );
}

function StageInspector({
  runGroups,
  stage,
  trace,
}: {
  runGroups: DeliveryRunGroup[];
  stage?: DeliveryTaskFlowStage;
  trace: DeliveryTrace;
}) {
  useSettingsPreferences();
  if (!stage) {
    return (
      <aside className="delivery-flow-inspector">
        <h3 className="section-title">{uiText("Stage Inspector")}</h3>
        <p className="muted">{uiText("Select a stage.")}</p>
      </aside>
    );
  }
  const workflowRun = workflowRunForStage(stage, trace.workflow_trace?.stage_runs ?? []);
  const aggregateWait = metricValue(workflowRun?.metrics, "aggregate_wait_ms");
  const rows = [
    ["stage", stage.stage_id],
    ["node", workflowRun?.node_id || "-"],
    ["status", stage.status],
    ["tasks", `${stage.tasks_done}/${stage.tasks_total}`],
    ["mode", runGroups.length ? "fanout/run" : "stage"],
    ["runs", runGroups.length],
    ["lanes", runGroups.reduce((total, run) => total + run.children.length, 0)],
    ["duration", formatDuration(workflowRun?.duration_ms)],
    ["queue", formatDuration(workflowRun?.queue_wait_ms)],
    ["aggregate", formatDuration(aggregateWait)],
    ["running", stage.tasks_running],
    ["blocked", stage.tasks_blocked ?? 0],
    ["events", stage.source_event_ids?.length ?? 0],
    ["trigger", workflowRun?.trigger_events?.join(", ") || "-"],
    ["output", workflowRun?.output_events?.join(", ") || "-"],
  ];
  return (
    <aside className="delivery-flow-inspector">
      <div className="inline-heading">
        <h3 className="section-title">{uiText("Stage Inspector")}</h3>
        <span className={`badge badge-${dtTone(stage.status)}`}>{stage.status}</span>
      </div>
      <dl className="delivery-inspector-grid">
        {rows.map(([key, value]) => (
          <Fragment key={String(key)}>
            <dt>{key}</dt>
            <dd className={String(key).includes("id") ? "mono" : ""}>{String(value || "-")}</dd>
          </Fragment>
        ))}
      </dl>
      <div className="workflow-inspector-block">
        <h4>{uiText("Gate / Verdict")}</h4>
        <dl className="delivery-inspector-grid">
          <dt>verdict</dt>
          <dd>{workflowRun?.verdict?.status || String(stage.gate_summary?.status ?? "-")}</dd>
          <dt>reason</dt>
          <dd>{workflowRun?.verdict?.reason || String(stage.gate_summary?.reason ?? "-")}</dd>
          <dt>evidence</dt>
          <dd className="mono">{workflowRun?.verdict?.evidence_event_id || "-"}</dd>
        </dl>
      </div>
      <div className="workflow-inspector-block">
        <h4>Refs</h4>
        <div className="workflow-chip-list">
          {(workflowRun?.source_event_ids ?? stage.source_event_ids ?? []).slice(-8).map((eventId) => (
            <code key={eventId}>{eventId}</code>
          ))}
          {workflowRun?.artifact_refs?.slice(0, 6).map((ref) => <code key={ref}>{ref}</code>)}
          {!(workflowRun?.source_event_ids?.length || stage.source_event_ids?.length || workflowRun?.artifact_refs?.length) && (
            <span className="muted">{uiText("No refs.")}</span>
          )}
        </div>
      </div>
      <DeliveryActionPlaceholders scope="stage" />
      <div className="workflow-inspector-block">
        <h4>{uiText("Run Groups")}</h4>
        <div className="workflow-chip-list">
          {runGroups.slice(0, 8).map((run) => <code key={run.group_id}>{run.group_id}</code>)}
          {!runGroups.length && <span className="muted">{uiText("No run groups.")}</span>}
        </div>
      </div>
    </aside>
  );
}

function runGroupsForStage(stage: DeliveryTaskFlowStage, runGroups: DeliveryRunGroup[]): DeliveryRunGroup[] {
  const ids = new Set(stage.run_group_ids);
  return runGroups.filter((run) => ids.has(run.group_id) || run.stage_id === stage.stage_id);
}

function stageDisplayLabel(stage: DeliveryTaskFlowStage): string {
  const source = String(stage.label || stage.stage_id || "stage").trim();
  const normalizedId = humanizeStageId(stage.stage_id);
  if (/fanout/i.test(source)) return normalizedId || source.replace(/fanout/ig, "").trim();
  return source;
}

function humanizeStageId(stageId: string): string {
  return stageId
    .replace(/[_-]?fanout/ig, "")
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join(" ");
}

function stageSummary(stage: DeliveryTaskFlowStage, runGroups: DeliveryRunGroup[]): string {
  const lanes = runGroups.reduce((total, run) => total + run.children.length, 0);
  if (lanes > 0) {
    const done = runGroups.reduce(
      (total, run) => total + run.children.filter((child) => ["done", "passed", "ok", "completed"].includes(String(child.status ?? ""))).length,
      0,
    );
    return `${done}/${lanes} lanes`;
  }
  if (stage.tasks_total > 0) return `${stage.tasks_done}/${stage.tasks_total} tasks`;
  return stage.status || "pending";
}

function workflowRunForStage(
  stage: DeliveryTaskFlowStage,
  runs: DeliveryWorkflowStageRun[],
): DeliveryWorkflowStageRun | undefined {
  return runs.find((run) => baseStageId(run.stage_id) === stage.stage_id || run.stage_id === stage.stage_id);
}

function baseStageId(stageId: string): string {
  return stageId.endsWith(":aggregate") ? stageId.slice(0, -10) : stageId;
}

function metricValue(metrics: Record<string, number | string | null> | undefined, key: string): number | null {
  const value = metrics?.[key];
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim() && !Number.isNaN(Number(value))) return Number(value);
  return null;
}

function DeliveryActionPlaceholders({ scope }: { scope: "stage" | "run" }) {
  useSettingsPreferences();
  const labels = scope === "stage"
    ? ["Retry stage", "Pause stage", "Resume stage"]
    : ["Rerun failed children", "Retry run", "Request fanout"];
  return (
    <div className="workflow-inspector-block">
      <h4>{uiText("Controlled Actions")}</h4>
      <div className="delivery-inspector-actions">
        {labels.map((label) => (
          <button key={label} type="button" className="delivery-action-button" disabled>
            {label}
          </button>
        ))}
      </div>
      <small className="delivery-action-note">
        {uiText("Read-only placeholder. Requires token-gated deterministic kernel action path.")}
      </small>
    </div>
  );
}
