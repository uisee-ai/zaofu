// Delivery Trace page (doc 68 S3 frontend). Read-only view over the
// delivery-trace.v1 API: feature spine status, ship readiness, execution
// graph by wave, and drift report. Self-contained — fetches on feature
// selection; no runtime mutation.
import { useEffect, useMemo, useRef, useState } from "react";
import { ListTodo, PlayCircle, Radio, Route } from "lucide-react";

import { getDeliveryTrace, getWorkflowGraph } from "../../api/client";
import type { WorkflowGraph } from "../../api/types";
import type {
  DeliveryFlowMetrics,
  DeliveryTrace,
  DeliveryTraceCycle,
  DeliveryTracePhase,
  RecentEvent,
} from "../../api/types";
import type { PageId } from "../../app/sharedTypes";
import { DeliveryOverview } from "./DeliveryOverview";
import { DeliveryThickGraphView } from "./DeliveryThickGraphView";
import { DeliveryTraceTabs, StageHeatmap } from "./DeliveryTraceTabs";

interface DeliveryTracePageProps {
  onOpenPage?: (page: PageId) => void;
  projectId: string;
  featureIds: string[];
  liveEvents?: RecentEvent[];
  mode?: "overview" | "trace" | "graph";
}

function dtTone(status: string): "ok" | "warn" | "err" | "info" | "muted" {
  if (["done", "passed", "ok", "ready", "shipped", "satisfied"].includes(status)) return "ok";
  if (["blocked", "failed", "error", "rejected"].includes(status)) return "err";
  if (["in_progress", "running"].includes(status)) return "info";
  if (["warning", "waiting", "pending", "needs_recovery", "rework"].includes(status)) return "warn";
  return "muted";
}

function currentPhase(trace: DeliveryTrace): DeliveryTracePhase | null {
  const phases = trace.phases ?? [];
  if (phases.length === 0) return null;
  return phases.find((phase) => !["done", "passed", "completed", "shipped"].includes(phase.status)) ?? phases[phases.length - 1] ?? null;
}

function phaseAsCycle(phase: DeliveryTracePhase): DeliveryTraceCycle {
  return {
    cycle_id: `phase:${phase.phase_id}`,
    kind: "planned_phase",
    phase: phase.phase_id,
    order: phase.order,
    status: phase.status,
    gate: phase.eval.verdict,
    task_ids: phase.task_ids,
    task_count: phase.task_count,
    done_count: phase.done_count,
    completion_rate: phase.completion_rate,
    pass_rate: phase.pass_rate,
    rework_count: phase.rework_count,
    paused_count: phase.paused_count,
  };
}

function traceCycles(trace: DeliveryTrace): DeliveryTraceCycle[] {
  if ((trace.cycles?.length ?? 0) > 0) return trace.cycles!;
  return (trace.phases ?? []).map(phaseAsCycle);
}

function currentCycle(trace: DeliveryTrace): DeliveryTraceCycle | null {
  const cycles = traceCycles(trace);
  if (cycles.length === 0) return null;
  return cycles.find((cycle) => !["done", "passed", "completed", "shipped", "adopted", "integrated"].includes(cycle.status)) ?? cycles[cycles.length - 1] ?? null;
}

function activeGateLabel(trace: DeliveryTrace, phase: DeliveryTracePhase | null, cycle: DeliveryTraceCycle | null): string {
  if (cycle?.gate && !["pending", "waiting"].includes(String(cycle.gate))) return `gate:${cycle.gate}`;
  if (trace.ship.status && trace.ship.status !== "unknown") return `ship:${trace.ship.status}`;
  if (phase?.eval?.verdict && phase.eval.verdict !== "pending") return `eval:${phase.eval.verdict}`;
  if (trace.drift_report.status && trace.drift_report.status !== "ok") return `drift:${trace.drift_report.status}`;
  return "ready-check";
}

function formatScoreDelta(delta: number | null | undefined): string {
  if (delta === null || delta === undefined) return "n/a";
  return `${delta >= 0 ? "+" : ""}${delta.toFixed(1)}`;
}

function cycleName(cycle: DeliveryTraceCycle): string {
  return String(cycle.phase || cycle.cycle_id || cycle.kind || "cycle");
}

// 2026-06-10 slice 1 — KPI rollup over flow_metrics.tasks (defensive: absent
// field → hasData false and both KPI cells stay hidden).

function metricValue(text: string, tone: string, testid?: string) {
  // R4 (2026-06-12): 值样式统一 —— 仅 warn/err 语义态上 badge,中性/ok 纯文本。
  if (tone === "warn" || tone === "err") {
    return <strong className={`badge badge-${tone}`} data-testid={testid}>{text}</strong>;
  }
  return <strong data-testid={testid}>{text}</strong>;
}

function flowRollup(metrics: DeliveryFlowMetrics | undefined): {
  backedges: number;
  reworkRatio: number | null;
  hasData: boolean;
} {
  const tasks = Object.values(metrics?.tasks ?? {});
  if (!tasks.length) return { backedges: 0, reworkRatio: null, hasData: false };
  let backedges = 0;
  let rework = 0;
  let total = 0;
  for (const task of tasks) {
    backedges += task?.backedge_count ?? 0;
    const segments =
      Math.max(0, task?.wait_seconds ?? 0)
      + Math.max(0, task?.active_seconds ?? 0)
      + Math.max(0, task?.rework_seconds ?? 0);
    rework += Math.max(0, task?.rework_seconds ?? 0);
    total += segments;
  }
  return { backedges, reworkRatio: total > 0 ? rework / total : null, hasData: true };
}

export function DeliveryTracePage({ onOpenPage, projectId, featureIds, liveEvents = [], mode = "overview" }: DeliveryTracePageProps) {
  const [selected, setSelected] = useState<string>(featureIds[0] ?? "");
  const [trace, setTrace] = useState<DeliveryTrace | null>(null);
  const [error, setError] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [cursorStatus, setCursorStatus] = useState("snapshot");
  // I5a: read-only per-role health heatmap on the Graph page too.
  const [graphAgg, setGraphAgg] = useState<WorkflowGraph | null>(null);
  useEffect(() => {
    if (mode !== "graph") return;
    let cancelled = false;
    getWorkflowGraph(projectId || "")
      .then((d) => { if (!cancelled) setGraphAgg(d); })
      .catch(() => { if (!cancelled) setGraphAgg(null); });
    return () => { cancelled = true; };
  }, [mode, projectId]);
  const lastEventIdRef = useRef("");
  const lastLiveSeqRef = useRef(0);
  const traceTaskIds = useMemo(() => new Set((trace?.execution_graph?.nodes ?? []).map((node) => node.task_id).filter(Boolean)), [trace]);

  const applyTrace = (t: DeliveryTrace, mode: "initial" | "poll" | "live") => {
    setTrace(t);
    lastEventIdRef.current = t.cursor?.last_event_id || lastEventIdRef.current;
    const deltaCount = t.cursor?.new_event_count ?? t.deltas?.length ?? 0;
    const degraded = t.cursor?.degraded;
    if (degraded) {
      setCursorStatus("cursor degraded");
    } else if (mode === "poll") {
      setCursorStatus(deltaCount ? `${deltaCount} updates` : "live");
    } else if (mode === "live") {
      setCursorStatus(deltaCount ? `${deltaCount} live updates` : "live");
    } else {
      setCursorStatus(t.cursor?.last_event_id ? "cursor ready" : "snapshot");
    }
  };

  useEffect(() => {
    if (featureIds.length && !featureIds.includes(selected)) {
      setSelected(featureIds[0]);
    }
  }, [featureIds, selected]);

  useEffect(() => {
    if (!selected) {
      setTrace(null);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof window.setInterval> | undefined;
    lastEventIdRef.current = "";
    setLoading(true);
    setError("");
    getDeliveryTrace(selected, projectId || undefined)
      .then((t) => {
        if (!cancelled) applyTrace(t, "initial");
      })
      .catch((e) => {
        if (!cancelled) setError(String(e?.message ?? e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    timer = window.setInterval(() => {
      const since = lastEventIdRef.current;
      if (!since) return;
      getDeliveryTrace(selected, projectId || undefined, since)
        .then((t) => {
          if (!cancelled) applyTrace(t, "poll");
        })
        .catch((e) => {
          if (!cancelled) setCursorStatus(`poll error: ${String(e?.message ?? e)}`);
        });
    }, 5000);
    return () => {
      cancelled = true;
      if (timer) window.clearInterval(timer);
    };
  }, [selected, projectId]);

  useEffect(() => {
    if (!selected || !liveEvents.length) return;
    const event = liveEvents.find((candidate) => isDeliveryLiveEvent(candidate, selected, traceTaskIds));
    const seq = Number(event?.seq ?? 0);
    if (!event || !seq || seq <= lastLiveSeqRef.current) return;
    lastLiveSeqRef.current = seq;
    const since = lastEventIdRef.current;
    getDeliveryTrace(selected, projectId || undefined, since || undefined)
      .then((t) => applyTrace(t, "live"))
      .catch((e) => setCursorStatus(`live error: ${String(e?.message ?? e)}`));
  }, [liveEvents, projectId, selected, traceTaskIds]);

  const pageTitle = mode === "trace" ? "Trace" : mode === "graph" ? "Graph" : "Delivery";
  const pageSubtitle = mode === "trace"
    ? "timeline, waterfall, runs, and replay"
    : mode === "graph"
      ? "advanced topology and artifact analysis"
      : "feature delivery cockpit";

  return (
    <div className={`delivery-trace-page mode-${mode}`} data-testid="delivery-trace-page">
      <div className="section-heading">
        <div>
          <h2>{pageTitle}</h2>
          <span className="muted">{pageSubtitle}</span>
        </div>
      </div>

      {featureIds.length === 0 ? (
        <DeliveryEmptyCockpit onOpenPage={onOpenPage} />
      ) : (
        <div className="tab-row compact-tabs" aria-label="Feature selector">
          {featureIds.map((fid) => (
            <button
              key={fid}
              type="button"
              className={`tab-button ${fid === selected ? "active" : ""}`}
              onClick={() => setSelected(fid)}
            >
              {fid}
              {fid === selected && trace?.feature_id === fid && trace.workflow_archetype ? (
                <span className="badge dt-archetype-badge" title={`workflow archetype: ${trace.workflow_archetype}`}>
                  [{trace.workflow_archetype}]
                </span>
              ) : null}
            </button>
          ))}
        </div>
      )}

      {loading && <p className="muted">Loading…</p>}
      {error && <p className="error" data-testid="dt-error">{error}</p>}

      {trace && (() => {
        const phase = currentPhase(trace);
        const cycle = currentCycle(trace);
        const cycles = traceCycles(trace);
        const activeGate = activeGateLabel(trace, phase, cycle);
        const totalCycles = cycles.length || trace.phase_count || 0;
        // I5b: severity-aware drift badge (operator-actionable err vs info).
        const driftSummary = (trace.drift_report.summary ?? {}) as {
          error?: number; warning?: number; info?: number;
        };
        const driftErr = driftSummary.error ?? 0;
        const driftWarn = driftSummary.warning ?? 0;
        const driftInfo = driftSummary.info ?? 0;
        const driftParts: string[] = [];
        if (driftErr) driftParts.push(`${driftErr} err`);
        if (driftWarn) driftParts.push(`${driftWarn} warn`);
        if (driftInfo) driftParts.push(`${driftInfo} info`);
        const driftLabel = driftParts.length ? driftParts.join(" · ") : "ok";
        const driftTone = driftErr ? "error" : driftWarn ? "warn" : dtTone(trace.drift_report.status);
        const flow = flowRollup(trace.flow_metrics);
        return (
        <div className="delivery-trace-body">
          <section className="delivery-cockpit-hero" data-testid="delivery-cockpit-hero">
            {/* R1/R2 (2026-06-12): eyebrow 删除(页头拥有 cockpit 一词),
                三行标题压一行,trace id 改 copy chip 消除 "trace trace-" 结巴。 */}
            <div className="delivery-cockpit-title">
              <div className="dt-archetype-row">
                <h3 title={trace.feature_id}>{trace.feature_id}</h3>
                {trace.workflow_archetype && (
                  <span className="badge dt-archetype-badge" data-testid="dt-archetype" title={`workflow archetype: ${trace.workflow_archetype}`}>
                    [{trace.workflow_archetype}]
                  </span>
                )}
                <button
                  type="button"
                  className="dt-trace-chip"
                  title="copy trace id"
                  onClick={() => { void navigator.clipboard?.writeText(trace.trace_id); }}
                >
                  {trace.trace_id} ⧉
                </button>
              </div>
            </div>
            <div className="delivery-cockpit-metrics" aria-label="Delivery status summary">
              <div className="delivery-cockpit-metric">
                <span>Status</span>
                {metricValue(trace.status, dtTone(trace.status))}
              </div>
              <div className="delivery-cockpit-metric">
                <span>Cycle</span>
                <strong>{cycle ? cycleName(cycle) : (totalCycles ? `${totalCycles} cycles` : "trace")}</strong>
              </div>
              <div className="delivery-cockpit-metric">
                <span>Gate</span>
                {metricValue(activeGate, dtTone(activeGate))}
              </div>
              <div className="delivery-cockpit-metric">
                <span>Ship</span>
                {metricValue(trace.ship.status, dtTone(trace.ship.status))}
              </div>
              <div className="delivery-cockpit-metric">
                <span title="operator-actionable (err) vs developer-info (info)">Drift</span>
                {metricValue(driftLabel, driftTone)}
              </div>
              <div className="delivery-cockpit-metric">
                <span>Cursor</span>
                {metricValue(cursorStatus, trace.cursor?.degraded ? "err" : "info")}
              </div>
              {flow.hasData && (
                <div className="delivery-cockpit-metric" data-testid="dt-flow-backedges">
                  <span>Backedges</span>
                  {metricValue(String(flow.backedges), flow.backedges > 0 ? "warn" : "ok")}
                </div>
              )}
              {flow.hasData && (
                <div className="delivery-cockpit-metric" data-testid="dt-flow-rework">
                  <span>Rework</span>
                  <span title="Σrework / Σ(wait+active+rework)">
                    {metricValue(
                      flow.reworkRatio === null ? "—" : `${Math.round(flow.reworkRatio * 100)}%`,
                      flow.reworkRatio !== null && flow.reworkRatio >= 0.25 ? "warn" : "ok",
                    )}
                  </span>
                </div>
              )}
              {(trace.score_summary?.scored_cycle_count ?? 0) > 0 && (
                <div className="delivery-cockpit-metric" data-testid="dt-score-summary">
                  <span>Score</span>
                  <strong className={`badge badge-${(trace.score_summary?.latest?.score_delta ?? 0) >= 0 ? "ok" : "err"}`}>
                    {formatScoreDelta(trace.score_summary?.latest?.score_delta)}
                  </strong>
                </div>
              )}
              {trace.deposition_summary && trace.deposition_summary.replan_gate_status !== "none" && (
                <div className="delivery-cockpit-metric" data-testid="dt-deposition-summary">
                  <span>Replan</span>
                  <strong className={`badge badge-${trace.deposition_summary.owner_decision_required ? "err" : dtTone(trace.deposition_summary.replan_gate_status)}`}>
                    {trace.deposition_summary.replan_gate_status}
                    {trace.deposition_summary.owner_decision_required ? " · owner" : ""}
                  </strong>
                </div>
              )}
            </div>
          </section>

          {/* 2026-06-12 用户反馈:原 dt-summary 条整体删除 —— 左三 chip 复读
              cockpit-metrics 的 Status/Ship/Drift,右四计数在 Run Graph 组节点
              摘要与 Tasks tab 各有更好的家;同数据不二渲染。 */}
          {mode === "overview" ? (
            <DeliveryOverview onOpenPage={onOpenPage} trace={trace} />
          ) : mode === "graph" ? (
            <>
              <StageHeatmap graph={graphAgg} />
              <DeliveryThickGraphView onOpenPage={onOpenPage} trace={trace} />
            </>
          ) : (
            <DeliveryTraceTabs projectId={projectId} trace={trace} />
          )}

          {trace.diagnostics.length > 0 && (
            <ul className="delivery-trace-diagnostics muted">
              {trace.diagnostics.map((d, idx) => (
                <li key={idx}>! {d.kind}: {d.message}</li>
              ))}
            </ul>
          )}
        </div>
        );
      })()}
    </div>
  );
}

function isDeliveryLiveEvent(event: RecentEvent, featureId: string, taskIds: Set<string>): boolean {
  const type = event.type || "";
  if (!/^(task|feature|fanout|workflow|run|ship|review|test|judge|candidate)\./.test(type)) return false;
  const payload = event.payload ?? {};
  const payloadFeature = String(payload.feature_id || payload.pdd_id || "");
  if (payloadFeature && payloadFeature !== featureId) return false;
  const taskId = String(event.task_id || payload.task_id || "");
  if (taskIds.size && taskId && !taskIds.has(taskId)) return false;
  return true;
}

function DeliveryEmptyCockpit({ onOpenPage }: { onOpenPage?: DeliveryTracePageProps["onOpenPage"] }) {
  const emptyStages = [
    ["Plan", "waiting"],
    ["Impl", "waiting"],
    ["Aggregate", "waiting"],
    ["Verify", "waiting"],
    ["Done", "waiting"],
  ];
  return (
    <section className="delivery-empty-cockpit delivery-empty-state" data-testid="delivery-empty-cockpit">
      <div className="delivery-empty-cockpit-head">
        <div>
          <span className="eyebrow">Delivery Cockpit</span>
          <h3>No delivery trace yet</h3>
          <p className="muted">
            Feature traces appear after features, task execution, workflow spine events, or ship readiness records are written.
          </p>
        </div>
        <div className="projection-empty-actions">
          <button className="icon-button" type="button" onClick={() => onOpenPage?.("board")} disabled={!onOpenPage}>
            <ListTodo size={14} strokeWidth={1.8} aria-hidden="true" />
            Open Tasks
          </button>
          <button className="icon-button" type="button" onClick={() => onOpenPage?.("runs")} disabled={!onOpenPage}>
            <PlayCircle size={14} strokeWidth={1.8} aria-hidden="true" />
            Open Runs
          </button>
          <button className="icon-button" type="button" onClick={() => onOpenPage?.("events")} disabled={!onOpenPage}>
            <Radio size={14} strokeWidth={1.8} aria-hidden="true" />
            Open Events
          </button>
        </div>
      </div>
      <div className="delivery-main-tabs delivery-empty-tabs" aria-hidden="true">
        {["Run Graph", "Tasks", "Runs", "Trace", "Raw"].map((label, index) => (
          <span key={label} className={`delivery-main-tab ${index === 0 ? "active" : ""}`}>
            <span>{label}</span>
            <small>{index === 0 ? "pending" : "empty"}</small>
          </span>
        ))}
      </div>
      <div className="delivery-flow-workbench delivery-flow-layout-empty" aria-hidden="true">
        <section className="delivery-stage-line-panel">
          <div className="delivery-stage-line">
            {emptyStages.map(([label, status]) => (
              <div key={label} className={`delivery-stage-node ${label === "Impl" ? "active" : ""}`}>
                <span className="delivery-stage-point status-warn" />
                <strong>{label}</strong>
                <small>{status}</small>
              </div>
            ))}
          </div>
        </section>
        <div className="delivery-stage-detail-grid">
          <section className="delivery-flow-stage-panel">
            <div className="delivery-stage-panel-head">
              <div>
                <span className="eyebrow">Selected Stage</span>
                <h3>Impl</h3>
              </div>
              <div className="delivery-stage-panel-badges">
                <span className="badge badge-warn">waiting</span>
                <span className="badge">stage</span>
              </div>
            </div>
            <div className="delivery-stage-empty-lane">
              <Route size={16} strokeWidth={1.8} aria-hidden="true" />
              <div>
                <strong>No stage runs projected</strong>
                <p className="muted">Delivery will show run groups, fanout lanes, gates, and agent runs here.</p>
              </div>
            </div>
          </section>
          <aside className="delivery-flow-inspector">
            <div className="inline-heading">
              <h3 className="section-title">Inspector</h3>
              <span className="badge">empty</span>
            </div>
            <p className="muted">Select a feature after runtime writes delivery trace evidence.</p>
          </aside>
        </div>
      </div>
    </section>
  );
}
