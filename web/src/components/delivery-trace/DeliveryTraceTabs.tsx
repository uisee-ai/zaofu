// DeliveryTraceTabs — Delivery 页五 tab 工作台。四视图职责判别(设计依据):
//   Run Graph = 现在(历史压缩)    · Tasks = 对象
//   Runs      = 每一次尝试(不可变记录) · Trace = 时间真相(统一时间轴)
// Runs → Trace 联动 state(focusWindow/focusRunId)与 Run Graph ⛓ causation
// 回放 state 都提升到这里共享(Trace 树的 event 叶按链/窗高亮)。
import { Fragment, useEffect, useState } from "react";

import {
  fetchOverviewPulse,
  getRegressionCases,
  getWorkflowGraph,
  postAction,
} from "../../api/client";
import type { RegressionCase } from "../../api/client";
import type {
  DeliveryAutoresearchGraph,
  DeliveryRunGroup,
  DeliveryRunTraceSpan,
  DeliveryTaskFlowStage,
  DeliveryTrace,
  DeliveryTraceAutoresearchCycle,
  DeliveryWorkflowStageRun,
  OverviewPulse,
  WorkflowGraph,
} from "../../api/types";
import { DeliveryRunsTab } from "./DeliveryRunsTab";
import { DeliveryRawTab, DeliveryTasksTab } from "./DeliveryTraceSections";
import { copyText, dtTone, formatDuration, parseSpanSeq } from "./DeliveryTraceViewUtils";
import type { TraceFocus } from "./DeliveryTraceViewUtils";
import { FlowSpanTree } from "./FlowSpanTree";
import { LifecycleDrawer } from "./LifecycleDrawer";
import type { LifecycleDrawerTab } from "./LifecycleDrawer";
import { RunGraphView } from "./RunGraphView";
import type { CausalState } from "./RunGraphView";

type DeliveryTab = "flow" | "tasks" | "runs" | "trace" | "raw";

interface DrawerTarget {
  taskId: string;
  tab?: LifecycleDrawerTab;
  trySel?: number;
}

interface DeliveryTraceTabsProps {
  projectId?: string;
  trace: DeliveryTrace;
}

export function DeliveryTraceTabs({ projectId, trace }: DeliveryTraceTabsProps) {
  const [activeTab, setActiveTab] = useState<DeliveryTab>("flow");
  const [selectedStageId, setSelectedStageId] = useState("");
  const [selectedSpanId, setSelectedSpanId] = useState("");
  const [drawer, setDrawer] = useState<DrawerTarget | null>(null);
  // R-刀/T-刀 联动 state(提升共享):Runs 三锚 → Trace 焦点窗;⛓ 回放链。
  const [traceFocus, setTraceFocus] = useState<TraceFocus | null>(null);
  const [causal, setCausal] = useState<CausalState | null>(null);
  // T-刀①.5 — pool strip stuck count + Tasks "why" column share one
  // overview-pulse fetch; failure degrades to null (both consumers omit).
  const [pulse, setPulse] = useState<OverviewPulse | null>(null);
  const [graphAgg, setGraphAgg] = useState<WorkflowGraph | null>(null);
  const [captured, setCaptured] = useState<Set<string>>(new Set());
  const [cases, setCases] = useState<RegressionCase[]>([]);
  const [verdicts, setVerdicts] = useState<Record<string, boolean>>({});

  // design 101 §8 C — list captured regression cases (I1b) + refresh.
  const refreshCases = () =>
    getRegressionCases(projectId, trace.feature_id)
      .then((r) => setCases(r.cases ?? []))
      .catch(() => undefined);

  // capture a role's failing task as a deterministic regression case.
  const captureRole = (taskId: string) => {
    postAction(
      "capture-regression-case",
      {
        task_id: taskId,
        feature_id: trace.feature_id,
        assertions: ["rework==0", "scope_violation==0"],
      },
      projectId,
    )
      .then(() => {
        setCaptured((prev) => new Set(prev).add(taskId));
        refreshCases();
      })
      .catch(() => undefined);
  };

  // design 101 §8 E — replay a captured case (I1c); store pass/fail verdict.
  const replayCase = (caseId: string) => {
    postAction("replay-regression-case", { case_id: caseId }, projectId)
      .then((r) =>
        setVerdicts((prev) => ({
          ...prev,
          [caseId]: !!(r as { result?: { passed?: boolean } }).result?.passed,
        })),
      )
      .catch(() => undefined);
  };
  const stages = trace.task_flow?.stages ?? [];
  const runGroups = trace.run_groups ?? [];
  const spans = trace.trace?.spans ?? [];
  // S-E: run-chain.v1 drives the Run Graph; absent/no_stage_order falls back
  // to the legacy stage-line Flow rendering (kept below, not deleted).
  const runChain = trace.run_chain;
  const hasRunGraph = !!runChain && runChain.status !== "no_stage_order" && runChain.stages.length > 0;

  useEffect(() => {
    setDrawer(null);
    setTraceFocus(null);
    setCausal(null);
  }, [trace.feature_id]);

  useEffect(() => {
    let cancelled = false;
    fetchOverviewPulse(projectId || "")
      .then((data) => {
        if (!cancelled) setPulse(data);
      })
      .catch(() => {
        if (!cancelled) setPulse(null);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // design 101 §2 layer-2: config-level aggregate outcome heatmap.
  useEffect(() => {
    let cancelled = false;
    getWorkflowGraph(projectId || "")
      .then((data) => {
        if (!cancelled) setGraphAgg(data);
      })
      .catch(() => {
        if (!cancelled) setGraphAgg(null);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // design 101 §8 I1b: list captured regression cases for this feature.
  useEffect(() => {
    let cancelled = false;
    getRegressionCases(projectId, trace.feature_id)
      .then((r) => {
        if (!cancelled) setCases(r.cases ?? []);
      })
      .catch(() => {
        if (!cancelled) setCases([]);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, trace.feature_id]);

  useEffect(() => {
    const candidate =
      stages.find((stage) => trace.task_flow?.active_stage_ids.includes(stage.stage_id))
      ?? stages[0];
    if (candidate && !stages.some((stage) => stage.stage_id === selectedStageId)) {
      setSelectedStageId(candidate.stage_id);
    }
  }, [selectedStageId, stages, trace.task_flow?.active_stage_ids]);

  useEffect(() => {
    const candidate =
      spans.find((span) => ["failed", "running", "blocked"].includes(span.status))
      ?? spans[0];
    if (candidate && !spans.some((span) => span.span_id === selectedSpanId)) {
      setSelectedSpanId(candidate.span_id);
    }
  }, [selectedSpanId, spans]);

  const tabs: Array<{ id: DeliveryTab; label: string; meta: string }> = [
    { id: "flow", label: "Run Graph", meta: `${hasRunGraph ? runChain!.stages.length : stages.length} stages` },
    { id: "tasks", label: "Tasks", meta: `${trace.execution_graph.task_count} tasks` },
    { id: "runs", label: "Runs", meta: `${runGroups.length} groups` },
    { id: "trace", label: "Trace", meta: `${trace.trace?.span_count ?? spans.length} spans` },
    { id: "raw", label: "Raw", meta: "json" },
  ];

  return (
    <section className="delivery-tabbed-workbench" data-testid="delivery-tabs">
      <div className="delivery-main-tabs" role="tablist" aria-label="Delivery views">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            className={`delivery-main-tab ${activeTab === tab.id ? "active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
            data-testid={`delivery-tab-${tab.id}`}
          >
            <span>{tab.label}</span>
            <small>{tab.meta}</small>
          </button>
        ))}
      </div>

      {activeTab === "flow" && (
        <Fragment>
          <StageHeatmap
            graph={graphAgg}
            onPickRole={(taskId) => setDrawer({ taskId })}
            onCaptureRole={captureRole}
            captured={captured}
          />
          <RegressionCasesPanel
            cases={cases}
            verdicts={verdicts}
            onReplay={replayCase}
          />
          {hasRunGraph ? (
            <RunGraphView
              causal={causal}
              onCausalChange={setCausal}
              onSelectTask={(taskId) => setDrawer({ taskId })}
              projectId={projectId}
              pulse={pulse}
              trace={trace}
            />
          ) : (
            <DeliveryFlowTab
              selectedStageId={selectedStageId}
              setSelectedStageId={setSelectedStageId}
              trace={trace}
            />
          )}
        </Fragment>
      )}
      {activeTab === "tasks" && (
        <DeliveryTasksTab
          onOpenDispatch={(taskId, trySel) => setDrawer({ taskId, tab: "events", trySel })}
          onSelectTask={(taskId) => setDrawer({ taskId })}
          pulse={pulse}
          trace={trace}
        />
      )}
      {activeTab === "runs" && (
        <DeliveryRunsTab
          onViewInTrace={(focus) => {
            setTraceFocus(focus);
            setActiveTab("trace");
          }}
          trace={trace}
        />
      )}
      {activeTab === "trace" && (
        <>
          <div className="dt-trace-layout" data-testid="delivery-trace-tab">
            <FlowSpanTree
              causalIds={causal?.ids ?? null}
              focus={traceFocus}
              onSelectSpan={setSelectedSpanId}
              selectedSpanId={selectedSpanId}
              trace={trace}
            />
            <SpanInspector span={spans.find((span) => span.span_id === selectedSpanId) ?? spans[0]} />
          </div>
          <AutoresearchGraphStrip
            cycles={trace.autoresearch_cycles ?? []}
            graphs={trace.trace?.autoresearch_graphs ?? []}
          />
        </>
      )}
      {activeTab === "raw" && <DeliveryRawTab trace={trace} />}
      {drawer && (activeTab === "flow" || activeTab === "tasks") && (
        <LifecycleDrawer
          key={`${drawer.taskId}:${drawer.tab ?? ""}:${drawer.trySel ?? ""}`}
          initialTab={drawer.tab}
          initialTry={drawer.trySel}
          onClose={() => setDrawer(null)}
          taskId={drawer.taskId}
          trace={trace}
        />
      )}
    </section>
  );
}

// design 101 §2 layer-2 — config-level aggregate outcome heatmap strip.
// Renders per-role pass_rate / rework_count / cost_usd from the
// workflow_graph projection, sorted weakest-first (loop targets).
// Exported so the Graph page can show it read-only (I5a).
export function StageHeatmap({
  graph,
  onPickRole,
  onCaptureRole,
  captured,
}: {
  graph: WorkflowGraph | null;
  onPickRole?: (taskId: string) => void;
  onCaptureRole?: (taskId: string) => void;
  captured?: Set<string>;
}) {
  if (!graph) return null;
  type HeatRow = {
    id?: string;
    label?: string;
    pass_rate?: number | null;
    rework_count?: number;
    cost_usd?: number | null;
    scope_violation_rate?: number | null;
    discriminator_catch_rate?: number | null;
    drill_task_id?: string | null;
  };
  const roles = (graph.nodes ?? []).filter(
    (n) => (n as { kind?: string }).kind === "role",
  ) as HeatRow[];
  // I2: dedupe by role-type (replicas share a name). pass/rework/cost are
  // consistent across instances; quality (scope/D-catch) is split by event
  // attribution, so take the non-null value.
  const byLabel = new Map<string, HeatRow>();
  for (const n of roles) {
    const label = String(n.label || n.id || "");
    const prev = byLabel.get(label);
    if (!prev) {
      byLabel.set(label, { ...n, label });
      continue;
    }
    const pick = <T,>(a: T | null | undefined, b: T | null | undefined) =>
      a ?? b ?? null;
    byLabel.set(label, {
      ...prev,
      pass_rate: pick(prev.pass_rate, n.pass_rate),
      rework_count: Math.max(prev.rework_count ?? 0, n.rework_count ?? 0),
      cost_usd: pick(prev.cost_usd, n.cost_usd),
      scope_violation_rate: pick(prev.scope_violation_rate, n.scope_violation_rate),
      discriminator_catch_rate: pick(prev.discriminator_catch_rate, n.discriminator_catch_rate),
      drill_task_id: pick(prev.drill_task_id, n.drill_task_id),
    });
  }
  const fmtPct = (v: number | null | undefined) =>
    typeof v === "number" ? `${Math.round(v * 100)}%` : "—";
  const fmtCost = (v: number | null | undefined) =>
    typeof v === "number" ? `$${v.toFixed(2)}` : "—";
  const heat = (v: number | null | undefined) =>
    typeof v === "number" ? (v < 0.6 ? "🟥" : v < 0.85 ? "🟧" : "·") : "·";
  // I3: capture only makes sense for a role that actually failed.
  const isFailing = (r: HeatRow) =>
    (r.rework_count ?? 0) > 0 ||
    (r.scope_violation_rate ?? 0) > 0 ||
    (r.discriminator_catch_rate ?? 0) > 0;
  const rows = [...byLabel.values()].sort(
    (a, b) =>
      (typeof a.pass_rate === "number" ? a.pass_rate : 2) -
      (typeof b.pass_rate === "number" ? b.pass_rate : 2),
  );
  // I4: hide quality columns that have no data across any role.
  const hasCost = rows.some((r) => typeof r.cost_usd === "number");
  const hasScope = rows.some((r) => typeof r.scope_violation_rate === "number");
  const hasDcatch = rows.some((r) => typeof r.discriminator_catch_rate === "number");
  return (
    <section
      className="graph-stage-heatmap"
      data-testid="graph-stage-heatmap"
      style={{
        border: "1px solid var(--border, #2a2a2a)",
        borderRadius: 6,
        padding: "8px 10px",
        marginBottom: 10,
        fontSize: 12,
      }}
    >
      <header style={{ opacity: 0.8, marginBottom: 6 }}>
        Stage Heatmap <small style={{ opacity: 0.6 }}>aggregate outcome by role · 🟥 weak 🟧 watch</small>
      </header>
      {rows.length ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
          {rows.map((node) => (
            <div
              key={node.id}
              data-testid="graph-stage-heatmap-row"
              role={node.drill_task_id ? "button" : undefined}
              title={node.drill_task_id ? "drill to this role's trace" : undefined}
              onClick={
                node.drill_task_id
                  ? () => onPickRole?.(node.drill_task_id as string)
                  : undefined
              }
              style={{
                display: "flex",
                gap: 6,
                alignItems: "baseline",
                cursor: node.drill_task_id ? "pointer" : "default",
              }}
            >
              <span>{heat(node.pass_rate)}</span>
              <strong>{node.label}</strong>
              <span style={{ opacity: 0.75 }}>pass {fmtPct(node.pass_rate)}</span>
              <span style={{ opacity: 0.75 }}>rw {node.rework_count ?? 0}</span>
              {hasCost ? (
                <span style={{ opacity: 0.75 }}>{fmtCost(node.cost_usd)}</span>
              ) : null}
              {hasScope ? (
                <span style={{ opacity: 0.6 }} title="scope violation rate">
                  scope {fmtPct(node.scope_violation_rate)}
                </span>
              ) : null}
              {hasDcatch ? (
                <span style={{ opacity: 0.6 }} title="discriminator catch rate">
                  D-catch {fmtPct(node.discriminator_catch_rate)}
                </span>
              ) : null}
              {node.drill_task_id && onCaptureRole && isFailing(node) ? (
                <button
                  type="button"
                  data-testid="graph-capture-btn"
                  onClick={(e) => {
                    e.stopPropagation();
                    onCaptureRole(node.drill_task_id as string);
                  }}
                  style={{ fontSize: 11, padding: "0 6px", cursor: "pointer" }}
                  title="capture this failure as a deterministic regression case"
                >
                  {captured?.has(node.drill_task_id) ? "✓ captured" : "capture"}
                </button>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <div style={{ opacity: 0.6 }}>no role data</div>
      )}
    </section>
  );
}

// design 101 §8 I1b/I1c — list captured regression cases + replay them.
function RegressionCasesPanel({
  cases,
  verdicts,
  onReplay,
}: {
  cases: RegressionCase[];
  verdicts: Record<string, boolean>;
  onReplay: (caseId: string) => void;
}) {
  if (!cases.length) return null;
  return (
    <section
      className="regression-cases"
      data-testid="regression-cases"
      style={{
        border: "1px solid var(--border, #2a2a2a)",
        borderRadius: 6,
        padding: "8px 10px",
        marginBottom: 10,
        fontSize: 12,
      }}
    >
      <header style={{ opacity: 0.8, marginBottom: 6 }}>
        Regression Cases{" "}
        <small style={{ opacity: 0.6 }}>
          {cases.length} captured · deterministic assertions
        </small>
      </header>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {cases.map((c) => {
          const v = verdicts[c.case_id];
          return (
            <div
              key={c.case_id}
              data-testid="regression-case-row"
              style={{ display: "flex", gap: 8, alignItems: "baseline" }}
            >
              <strong>{c.case_id}</strong>
              <span style={{ opacity: 0.7 }}>task {c.source_task_id}</span>
              <span style={{ opacity: 0.6 }}>
                {(c.assertions ?? []).join(", ") || "—"}
              </span>
              <button
                type="button"
                data-testid="regression-replay-btn"
                onClick={() => onReplay(c.case_id)}
                style={{ fontSize: 11, padding: "0 6px", cursor: "pointer" }}
                title="replay assertions against current state"
              >
                replay
              </button>
              {v !== undefined ? (
                <span
                  data-testid="regression-verdict"
                  style={{ color: v ? "#16a34a" : "#dc2626" }}
                >
                  {v ? "✓ pass" : "✗ fail"}
                </span>
              ) : null}
            </div>
          );
        })}
      </div>
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
  const stages = trace.task_flow?.stages ?? [];
  const runGroups = trace.run_groups ?? [];
  const selected = stages.find((stage) => stage.stage_id === selectedStageId) ?? stages[0];
  if (!stages.length) {
    return (
      <div className="delivery-tab-empty">
        <strong>No task-flow projection.</strong>
        <span className="muted">Delivery can still render task graph and raw workflow trace.</span>
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
            <p className="muted">Select a stage.</p>
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
          <span className="eyebrow">Selected Stage</span>
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
  if (!stage.tasks.length) {
    return (
      <div className="delivery-stage-empty-lane">
        <span className={`workflow-status-dot status-${dtTone(stage.status)}`} />
        <div>
          <strong>{stage.label || stage.stage_id}</strong>
          <p className="muted">No tasks currently mapped to this stage.</p>
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
              <strong>No child lanes projected</strong>
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
  if (!stage) {
    return (
      <aside className="delivery-flow-inspector">
        <h3 className="section-title">Stage Inspector</h3>
        <p className="muted">Select a stage.</p>
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
        <h3 className="section-title">Stage Inspector</h3>
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
        <h4>Gate / Verdict</h4>
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
            <span className="muted">No refs.</span>
          )}
        </div>
      </div>
      <DeliveryActionPlaceholders scope="stage" />
      <div className="workflow-inspector-block">
        <h4>Run Groups</h4>
        <div className="workflow-chip-list">
          {runGroups.slice(0, 8).map((run) => <code key={run.group_id}>{run.group_id}</code>)}
          {!runGroups.length && <span className="muted">No run groups.</span>}
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

// Span Inspector(T-刀④ 保留并扩展)— 点 event 叶后在 Trace 树右侧显示:
// seq(从 span_id `event:{seq}` 解析,⧉ copy = Observability 锚)、payload
// 字段、causation/raw event refs。Exported for the SSR smoke fixture.
export function SpanInspector({ span }: { span?: DeliveryRunTraceSpan }) {
  if (!span) return <aside className="delivery-span-inspector">Select a span.</aside>;
  const seq = parseSpanSeq(span.span_id);
  return (
    <aside className="delivery-span-inspector" data-testid="dt-span-inspector">
      <div className="inline-heading">
        <h3 className="section-title">Span Inspector</h3>
        <span className={`badge badge-${dtTone(span.status)}`}>{span.status}</span>
      </div>
      <dl className="delivery-inspector-grid">
        {[
          ["span", span.span_id],
          ["parent", span.parent_span_id || "-"],
          ["task", span.task_id || "-"],
          ["run", span.run_id || "-"],
          ["backend", span.backend || "-"],
          ["status", span.status],
          ["duration", formatDuration(span.duration_ms)],
          ["tokens", `${span.tokens_input ?? 0}/${span.tokens_output ?? 0}`],
          ["tools", span.tools_count ?? 0],
          ["events", span.raw_event_refs?.length ?? 0],
        ].map(([key, value]) => (
          <Fragment key={String(key)}>
            <dt>{key}</dt>
            <dd>{String(value || "-")}</dd>
          </Fragment>
        ))}
        <dt>seq</dt>
        <dd>
          {seq !== null ? (
            <button
              type="button"
              className="dt-anchor"
              title={`seq ${seq} — click copies it; paste into the Observability seq filter`}
              onClick={() => copyText(String(seq))}
            >
              {seq} ⧉
            </button>
          ) : (
            <span className="muted" title="span id is not event:{seq} shaped">—</span>
          )}
        </dd>
      </dl>
      <div className="workflow-inspector-block">
        <h4>Causation / Refs</h4>
        <div className="workflow-chip-list">
          {(span.raw_event_refs ?? []).slice(0, 8).map((ref) => <code key={ref} title={ref}>{ref}</code>)}
          {(span.evidence_refs ?? []).slice(0, 6).map((ref) => <code key={ref} title={ref}>{ref}</code>)}
          {!(span.raw_event_refs?.length || span.evidence_refs?.length) && <span className="muted">No refs.</span>}
        </div>
      </div>
      {span.error && Object.keys(span.error).length > 0 ? (
        <pre className="delivery-raw-block">{JSON.stringify(span.error, null, 2)}</pre>
      ) : null}
    </aside>
  );
}

function AutoresearchGraphStrip({
  cycles,
  graphs,
}: {
  cycles: DeliveryTraceAutoresearchCycle[];
  graphs: DeliveryAutoresearchGraph[];
}) {
  if (!graphs.length && !cycles.length) return null;
  return (
    <section className="delivery-autoresearch-graphs">
      <div className="inline-heading">
        <h3 className="section-title">Autoresearch</h3>
        <span className="muted">{graphs.length || cycles.length} loops</span>
      </div>
      <div className="delivery-autoresearch-graph-grid">
        {graphs.map((graph) => (
          <article key={graph.graph_id} className="delivery-autoresearch-graph">
            <div>
              <strong>{graph.graph_id}</strong>
              <span className={`badge badge-${dtTone(graph.status)}`}>{graph.status}</span>
              <span className="badge">{graph.comparison_mode}</span>
            </div>
            <div className="delivery-autoresearch-node-row">
              {graph.nodes.slice(0, 8).map((node, index) => (
                <Fragment key={`${String(node.node_id ?? index)}`}>
                  {index > 0 && <span className="dt-arrow">→</span>}
                  <code>{String(node.kind ?? node.event_type ?? "-")}</code>
                </Fragment>
              ))}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
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
  const labels = scope === "stage"
    ? ["Retry stage", "Pause stage", "Resume stage"]
    : ["Rerun failed children", "Retry run", "Request fanout"];
  return (
    <div className="workflow-inspector-block">
      <h4>Controlled Actions</h4>
      <div className="delivery-inspector-actions">
        {labels.map((label) => (
          <button key={label} type="button" className="delivery-action-button" disabled>
            {label}
          </button>
        ))}
      </div>
      <small className="delivery-action-note">
        Read-only placeholder. Requires token-gated deterministic kernel action path.
      </small>
    </div>
  );
}
