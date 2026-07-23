// Delivery Trace page (doc 68 S3 frontend). Read-only view over the
// delivery-trace.v1 API: feature spine status, ship readiness, execution
// graph by wave, and drift report. Self-contained — fetches on feature
// selection; no runtime mutation.
import { useEffect, useMemo, useRef, useState } from "react";
import { ListTodo, PlayCircle, Radio, Route } from "lucide-react";

import { getDeliveryTrace, getWorkflowGraph } from "../../api/client";
import type {
  DeliveryFlowMetrics,
  DeliveryTrace,
  DeliveryTraceCycle,
  DeliveryTracePhase,
  Feature,
  RecentEvent,
  WorkflowGraph,
} from "../../api/types";
import { LatestRequestGate } from "../../app/latestRequestGate";
import type { PageId } from "../../app/sharedTypes";
import { DeliveryMapView } from "./DeliveryMapView";
import { DeliveryOverview } from "./DeliveryOverview";
import { DeliveryTraceTabs } from "./DeliveryTraceTabs";

interface DeliveryTracePageProps {
  onOpenPage?: (page: PageId) => void;
  onSelectTask?: (taskId: string) => void;
  projectId: string;
  features: Feature[];
  liveEvents?: RecentEvent[];
  mode?: "overview" | "trace" | "graph";
  totalUsd?: number;
}

// PM 快赢(2026-07-11):hero 需要一眼判决 + 时长 + 成本。
function heroVerdict(trace: DeliveryTrace): { label: string; tone: "ok" | "err" | "info" | "warn" } {
  const ship = trace.ship.status;
  if (["blocked", "failed", "error"].includes(ship)) return { label: "🔴 Blocked", tone: "err" };
  if (["done", "shipped"].includes(trace.status) && ["ready", "shipped"].includes(ship)) {
    return { label: ship === "shipped" ? "✅ Shipped" : "✅ Ready to ship", tone: "ok" };
  }
  if (["in_progress", "running"].includes(trace.status)) return { label: "▶ Running", tone: "info" };
  return { label: trace.status || "unknown", tone: "warn" };
}

function heroDuration(trace: DeliveryTrace): string {
  const groups = trace.run_groups ?? [];
  const starts = groups.map((g) => Date.parse(g.started_at || "")).filter(Number.isFinite);
  const ends = groups.map((g) => Date.parse(g.ended_at || "")).filter(Number.isFinite);
  if (!starts.length) return "";
  const ms = (ends.length ? Math.max(...ends) : Date.now()) - Math.min(...starts);
  if (!(ms > 0)) return "";
  const minutes = Math.round(ms / 60000);
  return minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h${minutes % 60 ? `${minutes % 60}m` : ""}`;
}

// racing 评审:feature_list 为空时投影把每个 trace/fanout id 升格成伪
// feature(fallback:trace-ref / fallback:fanout-ref,11 个 rmar-* 淹没
// 1 个真 feature)。选择器按 source 分区:真 feature(task-contract /
// feature-list)主 chip,升格 trace 收进溢出下拉并翻译成人话。
const AUX_SOURCES = new Set(["fallback:trace-ref", "fallback:fanout-ref", "fallback:candidate-ref"]);

// rmar-*(Run Manager 修复请求)连溢出都不进:选中它 = 空驾驶舱
// (STATUS empty / SHIP unknown / 0 任务),Delivery 对它无话可说。
// 取证家在 Observability Trace Index / Loop 的 Run recovery 环;
// Overview 的 Latest Run 卡显示介入次数(operator 2026-07-11 决定)。
function isRepairTrace(id: string): boolean {
  return id.startsWith("rmar-");
}

function auxTraceGroup(id: string): string {
  if (/^fanout-flow-/.test(id)) return "Fanout 子流";
  if (id.startsWith("HIC-")) return "改进候选";
  return "其他";
}

function auxTraceLabel(id: string): string {
  const fanout = id.match(/^fanout-flow-(.+)-evt-[0-9a-f]+$/i);
  if (fanout) return `fanout: ${fanout[1]}`;
  return id;
}

function featureChipLabel(feature: Feature): string {
  const title = (feature.title || "").trim();
  if (!title || title === `Trace ${feature.id}`) return feature.id;
  return title.length > 48 ? `${title.slice(0, 47)}…` : title;
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
  // A4(racing 评审):phase 可能携带原始事件 id("rework:evt-72813b16a831"),
  // 操作员不可读 —— 去掉 :evt-* 尾缀,完整值在 Raw tab。
  const raw = String(cycle.phase || cycle.cycle_id || cycle.kind || "cycle");
  return raw.replace(/:evt-[0-9a-f]+$/i, "");
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

export function DeliveryTracePage({
  onOpenPage,
  onSelectTask,
  projectId,
  features,
  liveEvents = [],
  mode = "overview",
  totalUsd = 0,
}: DeliveryTracePageProps) {
  const primaryFeatures = features.filter((f) => !AUX_SOURCES.has(f.source || ""));
  const auxAll = features.filter((f) => AUX_SOURCES.has(f.source || ""));
  const auxTraces = auxAll.filter((f) => !isRepairTrace(f.id));
  const repairCount = auxAll.length - auxTraces.length;
  const [selected, setSelected] = useState<string>(primaryFeatures[0]?.id ?? features[0]?.id ?? "");
  const selectedFeature = features.find((f) => f.id === selected) ?? null;
  const selectedIsAux = AUX_SOURCES.has(selectedFeature?.source || "");
  const [trace, setTrace] = useState<DeliveryTrace | null>(null);
  const [error, setError] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [cursorStatus, setCursorStatus] = useState("snapshot");
  // Runs reuses workflow_graph for Stage Heatmap; the same response provides
  // the delivery-page cost fallback when the light snapshot has no agent_live.
  const [workflowGraph, setWorkflowGraph] = useState<WorkflowGraph | null>(null);
  const [fallbackUsd, setFallbackUsd] = useState(0);
  useEffect(() => {
    const needsWorkflowGraph = mode === "trace" || totalUsd <= 0;
    if (!projectId || !needsWorkflowGraph) {
      setWorkflowGraph(null);
      setFallbackUsd(0);
      return;
    }
    setWorkflowGraph(null);
    if (totalUsd <= 0) setFallbackUsd(0);
    let cancelled = false;
    getWorkflowGraph(projectId)
      .then((g) => {
        if (cancelled) return;
        setWorkflowGraph(g);
        const sum = (g?.nodes ?? []).reduce((acc, node) => {
          const cost = (node as { cost_usd?: number | null }).cost_usd;
          return acc + (typeof cost === "number" ? cost : 0);
        }, 0);
        setFallbackUsd(sum);
      })
      .catch(() => {
        if (cancelled) return;
        setWorkflowGraph(null);
        setFallbackUsd(0);
      });
    return () => { cancelled = true; };
  }, [mode, projectId, totalUsd]);
  const heroUsd = totalUsd > 0 ? totalUsd : fallbackUsd;
  const lastEventIdRef = useRef("");
  const lastLiveSeqRef = useRef(0);
  const traceRequestGateRef = useRef(new LatestRequestGate());
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
    if (features.length && !features.some((f) => f.id === selected)) {
      setSelected(primaryFeatures[0]?.id ?? features[0].id);
    }
  }, [features, primaryFeatures, selected]);

  useEffect(() => {
    if (!selected) {
      setTrace(null);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof window.setInterval> | undefined;
    traceRequestGateRef.current.invalidate();
    lastEventIdRef.current = "";
    setLoading(true);
    setError("");
    const initialTicket = traceRequestGateRef.current.issue();
    getDeliveryTrace(selected, projectId || undefined)
      .then((t) => {
        if (!cancelled && traceRequestGateRef.current.isCurrent(initialTicket)) {
          applyTrace(t, "initial");
          setLoading(false);
        }
      })
      .catch((e) => {
        if (!cancelled && traceRequestGateRef.current.isCurrent(initialTicket)) {
          setError(String(e?.message ?? e));
          setLoading(false);
        }
      });
    timer = window.setInterval(() => {
      const since = lastEventIdRef.current;
      if (!since) return;
      const ticket = traceRequestGateRef.current.issue();
      getDeliveryTrace(selected, projectId || undefined, since)
        .then((t) => {
          if (!cancelled && traceRequestGateRef.current.isCurrent(ticket)) {
            applyTrace(t, "poll");
            setLoading(false);
          }
        })
        .catch((e) => {
          if (!cancelled && traceRequestGateRef.current.isCurrent(ticket)) {
            setCursorStatus(`poll error: ${String(e?.message ?? e)}`);
            setLoading(false);
          }
        });
    }, 5000);
    return () => {
      cancelled = true;
      traceRequestGateRef.current.invalidate();
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
    const ticket = traceRequestGateRef.current.issue();
    getDeliveryTrace(selected, projectId || undefined, since || undefined)
      .then((t) => {
        if (!traceRequestGateRef.current.isCurrent(ticket)) return;
        applyTrace(t, "live");
        setLoading(false);
      })
      .catch((e) => {
        if (!traceRequestGateRef.current.isCurrent(ticket)) return;
        setCursorStatus(`live error: ${String(e?.message ?? e)}`);
        setLoading(false);
      });
  }, [liveEvents, projectId, selected, traceTaskIds]);

  const pageTitle = mode === "trace" ? "Runs" : mode === "graph" ? "Graph" : "Delivery";
  const pageSubtitle = mode === "trace"
    ? "run state, spans, and stage quality"
    : mode === "graph"
      ? "coverage, work, and diagnostics"
      : "feature delivery cockpit";

  return (
    <div className={`delivery-trace-page mode-${mode}`} data-testid="delivery-trace-page">
      <div className="section-heading">
        <div>
          <h2>{pageTitle}</h2>
          <span className="muted">{pageSubtitle}</span>
        </div>
      </div>

      {features.length === 0 ? (
        <DeliveryEmptyCockpit onOpenPage={onOpenPage} />
      ) : (primaryFeatures.length >= 2 || selectedIsAux || auxTraces.length > 0) && (
        <div className="tab-row compact-tabs dt-feature-selector" aria-label="Feature selector">
          {/* 单 feature 且未落在辅助 trace 上时不渲染 chip(标题在 hero);
              多 feature 或当前选中辅助 trace 时渲染,保证有路回真 feature。 */}
          {(primaryFeatures.length >= 2 || selectedIsAux) && primaryFeatures.map((feature) => (
            <button
              key={feature.id}
              type="button"
              className={`tab-button ${feature.id === selected ? "active" : ""}`}
              title={feature.id}
              onClick={() => setSelected(feature.id)}
            >
              {featureChipLabel(feature)}
            </button>
          ))}
          {auxTraces.length > 0 && (
            <details className="dt-trace-overflow" data-testid="dt-trace-overflow">
              <summary className={selectedIsAux ? "active" : ""}>
                {selectedIsAux ? `${auxTraceLabel(selected)} · ` : ""}其他 trace ({auxTraces.length})
              </summary>
              <div className="dt-trace-overflow-panel">
                {[...new Set(auxTraces.map((f) => auxTraceGroup(f.id)))].map((group) => (
                  <div key={group}>
                    <div className="dt-trace-overflow-group muted">{group}</div>
                    {auxTraces.filter((f) => auxTraceGroup(f.id) === group).map((f) => (
                      <button
                        key={f.id}
                        type="button"
                        className={`dt-trace-overflow-item ${f.id === selected ? "active" : ""}`}
                        onClick={() => setSelected(f.id)}
                      >
                        {auxTraceLabel(f.id)}
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            </details>
          )}
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
                {/* 有真标题时显标题(F-db467ab6 → "core: vehicle/track/…"),
                    id 留 tooltip;fallback 伪 feature 仍显 id。 */}
                <h3 title={trace.feature_id}>
                  {selectedFeature && featureChipLabel(selectedFeature) !== selectedFeature.id
                    ? featureChipLabel(selectedFeature)
                    : trace.feature_id}
                </h3>
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
              {(() => { const v = heroVerdict(trace); return (
                <div className={`dt-verdict tone-${v.tone}`} data-testid="dt-verdict">{v.label}</div>
              ); })()}
              <div className="delivery-cockpit-metric">
                <span>Status</span>
                {metricValue(trace.status, dtTone(trace.status))}
              </div>
              <div className="delivery-cockpit-metric">
                <span>{["done", "shipped"].includes(trace.status) ? "Last cycle" : "Cycle"}</span>
                <strong>{cycle ? cycleName(cycle) : (totalCycles ? `${totalCycles} cycles` : "trace")}</strong>
              </div>
              {/* Gate 与 Ship 同值时只渲染 Ship(GATE ship:blocked + SHIP blocked
                  一屏两报,2026-07-11 Playwright 评审;同数据不二渲染)。 */}
              {activeGate !== `ship:${trace.ship.status}` && (
                <div className="delivery-cockpit-metric">
                  <span>Gate</span>
                  {metricValue(activeGate, dtTone(activeGate))}
                </div>
              )}
              <div className="delivery-cockpit-metric">
                <span>Ship</span>
                {metricValue(trace.ship.status, dtTone(trace.ship.status))}
              </div>
              {heroDuration(trace) && (
                <div className="delivery-cockpit-metric" data-testid="dt-duration">
                  <span>Duration</span>
                  <strong>{heroDuration(trace)}</strong>
                </div>
              )}
              {heroUsd > 0 && (
                <div className="delivery-cockpit-metric" data-testid="dt-cost">
                  <span title="project run cost (usage_by_role / workflow_graph total)">Cost</span>
                  <strong>${heroUsd.toFixed(2)}</strong>
                </div>
              )}
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
              cockpit-metrics 的 Status/Ship/Drift,同数据不二渲染。 */}
          {/* 2026-07-11 operator 决定(A 案):三导航项收敛为页内 mode tab。
              tab 切换走 onOpenPage 保留 page id 路由与深链。 */}
          <div className="tab-row compact-tabs" aria-label="Delivery mode" data-testid="dt-mode-tabs">
            {([["delivery", "overview", "Overview"], ["delivery-trace", "trace", "Runs"], ["delivery-graph", "graph", "Graph"]] as const).map(([pid, m, label]) => (
              <button
                key={m}
                type="button"
                className={`tab-button ${mode === m ? "active" : ""}`}
                data-testid={`dt-mode-tab-${m}`}
                onClick={() => onOpenPage?.(pid)}
              >
                {label}
              </button>
            ))}
          </div>
          {mode === "overview" ? (
            <DeliveryOverview trace={trace} repairCount={repairCount} onOpenPage={onOpenPage} />
          ) : mode === "graph" ? (
            <DeliveryMapView
              feature={selectedFeature ?? null}
              onOpenPage={onOpenPage}
              onSelectTask={onSelectTask}
              projectId={projectId}
              trace={trace}
            />
          ) : (
            <DeliveryTraceTabs
              onOpenPage={onOpenPage}
              projectId={projectId}
              trace={trace}
              workflowGraph={workflowGraph}
            />
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
        {["Run", "Spans"].map((label, index) => (
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
