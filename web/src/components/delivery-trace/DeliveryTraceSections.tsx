import { useMemo, useState } from "react";
import type { ReactNode } from "react";

import type {
  DeliveryFlowConvergenceRound,
  DeliveryFlowTaskMetrics,
  DeliveryTaskTry,
  DeliveryTrace,
  DeliveryTraceCycle,
  OverviewPulse,
} from "../../api/types";
import { SegBar, formatSeconds } from "../common/SegBar";
import {
  copyText,
  currentCycle,
  cycleMetaLabel,
  cycleName,
  dtTone,
  latestDispatchTry,
  shortDispatch,
  taskSeqRange,
  traceCycles,
} from "./DeliveryTraceViewUtils";

interface DeliveryTasksTabProps {
  onOpenDispatch?: (taskId: string, trySel?: number) => void;
  onSelectTask?: (taskId: string) => void;
  pulse?: OverviewPulse | null;
  trace: DeliveryTrace;
}

export function DeliveryTasksTab({ onOpenDispatch, onSelectTask, pulse, trace }: DeliveryTasksTabProps) {
  // S-C: list = execution-graph rows (列头常显); grid = task × try outcome matrix.
  const [view, setView] = useState<"list" | "grid">("list");
  return (
    <div className="delivery-tasks-tab" data-testid="delivery-tasks-tab">
      <div className="dt-tasks-toolbar">
        <div className="dt-view-toggle" data-testid="tasks-view-toggle" role="group" aria-label="Tasks view mode">
          {(["list", "grid"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              className={view === mode ? "active" : ""}
              aria-pressed={view === mode}
              onClick={() => setView(mode)}
            >
              {mode}
            </button>
          ))}
        </div>
      </div>
      {view === "list" ? (
        <ExecutionGraphSection onOpenDispatch={onOpenDispatch} onSelectTask={onSelectTask} pulse={pulse} trace={trace} />
      ) : (
        <TasksTryGridSection onSelectTask={onSelectTask} trace={trace} />
      )}
      <DriftSection trace={trace} />
    </div>
  );
}

export function DeliveryFlowContext({ trace }: { trace: DeliveryTrace }) {
  return (
    <div className="delivery-flow-context" data-testid="delivery-flow-context">
      <DeliveryCyclesSection cycles={traceCycles(trace)} trace={trace} />
      <WorkflowSpineSection trace={trace} />
      <PhasesSection trace={trace} />
    </div>
  );
}

export function DeliveryRawTab({ trace }: { trace: DeliveryTrace }) {
  return (
    <pre className="delivery-raw-block delivery-raw-tab" data-testid="delivery-raw-tab">
      {JSON.stringify({
        task_flow: trace.task_flow,
        run_groups: trace.run_groups,
        trace: trace.trace,
        workflow_trace: trace.workflow_trace,
        diagnostics: trace.diagnostics,
      }, null, 2)}
    </pre>
  );
}

function DeliveryCyclesSection({ cycles, trace }: { cycles: DeliveryTraceCycle[]; trace: DeliveryTrace }) {
  if (!cycles.length) {
    const taskCount = trace.task_map?.task_count ?? trace.execution_graph?.task_count ?? trace.execution_graph?.nodes.length ?? 0;
    const waveCount = trace.task_map?.wave_count
      ?? new Set(trace.execution_graph?.nodes.map((node) => node.planned.wave) ?? []).size;
    return (
      <section className="delivery-cycle-strip">
        <div className="inline-heading"><h3 className="section-title">Cycles</h3><span className="muted">fallback</span></div>
        <div className="delivery-cycle-items">
          <div className="delivery-cycle-item active">
            <span className="delivery-cycle-id mono">trace</span>
            <span className={`badge badge-${dtTone(trace.status)}`}>{trace.status}</span>
            <span className="delivery-cycle-meta">task-map {taskCount} tasks / {waveCount} waves</span>
          </div>
        </div>
      </section>
    );
  }
  const current = currentCycle(trace);
  return (
    <section className="delivery-cycle-strip" aria-label="Delivery cycles">
      <div className="inline-heading"><h3 className="section-title">Cycles</h3><span className="muted">{cycles.length} projected</span></div>
      <div className="delivery-cycle-items">
        {cycles.map((item) => (
          <div key={item.cycle_id} className={`delivery-cycle-item ${item.cycle_id === current?.cycle_id ? "active" : ""}`}>
            <span className="delivery-cycle-id mono">{cycleName(item)}</span>
            <span className="delivery-cycle-kind">{item.kind}</span>
            <span className={`badge badge-${dtTone(item.status)}`}>{item.status}</span>
            <span className="delivery-cycle-meta">{cycleMetaLabel(item)}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function WorkflowSpineSection({ trace }: { trace: DeliveryTrace }) {
  if (!trace.workflow_spine) return null;
  return (
    <section className="delivery-trace-section">
      <h3 className="section-title">Workflow Spine</h3>
      {trace.workflow_spine.nodes.length ? (
        <div className="delivery-trace-nodes">
          {trace.workflow_spine.nodes.slice(-24).map((node, index) => (
            <div key={`${String(node.event_id ?? index)}`} className="delivery-trace-node">
              <code className="dt-node-id">{String(node.kind ?? "-")}</code>
              <span className={`badge badge-${dtTone(String(node.status ?? ""))}`}>{String(node.status ?? "-")}</span>
              <span className="dt-node-meta">{String(node.task_id ?? "") || String(node.fanout_id ?? "") || String(node.event_type ?? "")}</span>
            </div>
          ))}
        </div>
      ) : <p className="muted">No workflow spine nodes.</p>}
    </section>
  );
}

function PhasesSection({ trace }: { trace: DeliveryTrace }) {
  if (!(trace.phases?.length)) return null;
  return (
    <section className="delivery-trace-section">
      <h3 className="section-title">Phases</h3>
      <div className="delivery-trace-phases">
        {trace.phases.map((phase) => (
          <div key={phase.phase_id} className="delivery-trace-phase">
            <div className="delivery-trace-phase-head">
              <span className="phase-label">{phase.phase_id}</span>
              <span className={`badge badge-${dtTone(phase.status)}`}>{phase.status}</span>
              <span className="badge badge-ok">完成 {Math.round(phase.completion_rate * 100)}%</span>
              <span className="badge">达标 {phase.pass_rate != null ? `${Math.round(phase.pass_rate * 100)}%` : "-"}</span>
              <span className={`badge badge-${dtTone(phase.eval.verdict)}`}>{phase.eval.verdict}</span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function ExecutionGraphSection({
  onOpenDispatch,
  onSelectTask,
  pulse,
  trace,
}: {
  onOpenDispatch?: (taskId: string, trySel?: number) => void;
  onSelectTask?: (taskId: string) => void;
  pulse?: OverviewPulse | null;
  trace: DeliveryTrace;
}) {
  const flowTasks = trace.flow_metrics?.tasks;
  const lifecycleTasks = trace.task_lifecycle?.tasks ?? {};
  // T-刀 Tasks 表同步 — why = overview-pulse why_not notifications joined on task_id.
  const whyByTask = useMemo(() => {
    const map = new Map<string, { kind: string; reason: string }>();
    for (const item of pulse?.why_not?.notifications ?? []) {
      const taskId = String((item as Record<string, unknown>).task_id ?? "");
      if (taskId && !map.has(taskId)) {
        map.set(taskId, { kind: String(item.kind ?? "?"), reason: String(item.reason ?? "") });
      }
    }
    return map;
  }, [pulse]);
  return (
    <section className="delivery-trace-section">
      <h3 className="section-title">Task Runtime</h3>
      {/* S-C 顺修: legend + 列头常显 — 无 flow_metrics 时行值显灰 "—",不再隐藏整列 */}
      <div className="dt-flow-legend muted" data-testid="dt-flow-legend">
        <span className="seg-chip seg-chip-wait" /> wait
        <span className="seg-chip seg-chip-active" /> active
        <span className="seg-chip seg-chip-rework" /> rework
        <span>· conv = pass-rate/round (✗ = stalled ≥2 rounds)</span>
      </div>
      {trace.execution_graph.waves.length === 0 ? (
        /* R5 (2026-06-12): 空态自解释 —— diagnostics 升格进正文,空白必须说明
           自己(同 no-data ≠ 0 纪律);页脚小字保留。 */
        <div className="projection-empty-copy" data-testid="dt-graph-empty">
          <p className="muted">No task runtime projection for this feature.</p>
          {trace.task_map?.status === "missing" && (
            <p className="muted">No accepted task_map yet — the graph appears after a plan/task_map is accepted.</p>
          )}
          {trace.diagnostics.slice(0, 3).map((d, idx) => (
            <p className="muted" key={idx}>{d.kind ? `${d.kind}: ` : ""}{d.message ?? ""}</p>
          ))}
        </div>
      ) : (
        <div className="delivery-trace-waves">
          <div className="dt-flow-header" data-testid="dt-flow-header" aria-hidden="true">
            <span className="dt-flow-cols">
              <span className="dt-flow-dispatch">dispatch</span>
              <span className="dt-flow-num">seq</span>
              <span className="dt-flow-why">why</span>
              <span className="dt-flow-num">q-wait</span>
              <span className="dt-flow-num">first-resp</span>
              <span className="dt-flow-num">active</span>
              <span className="dt-flow-mix">mix</span>
              <span className="dt-flow-num">backedge</span>
              <span className="dt-flow-conv">conv</span>
            </span>
          </div>
          {trace.execution_graph.waves.map((wave) => (
            <div key={wave.wave} className="delivery-trace-wave">
              <div className="delivery-trace-wave-head">
                <span className="wave-label">Wave {wave.wave}</span>
                <span className={`badge badge-${dtTone(wave.status)}`}>{wave.status}</span>
              </div>
              <div className="delivery-trace-nodes">
                {wave.task_ids.map((tid) => {
                  const node = trace.execution_graph.nodes.find((n) => n.task_id === tid);
                  return (
                    <button key={tid} type="button" className="delivery-trace-node dt-node-clickable" onClick={() => onSelectTask?.(tid)}>
                      <code className="dt-node-id" title={tid}>{tid}</code>
                      <span className={`badge badge-${dtTone(node?.actual.status ?? "")}`}>{node?.actual.status ?? "?"}</span>
                      <span className="dt-node-meta">{node?.planned.owner_role || "-"} <span className="dt-arrow">→</span> {node?.actual.assigned_to || "-"}</span>
                      <FlowCells
                        lead={
                          <>
                            <DispatchCell onOpenDispatch={onOpenDispatch} taskId={tid} tries={lifecycleTasks[tid]?.tries} />
                            <SeqCell tries={lifecycleTasks[tid]?.tries} />
                            <WhyCell why={whyByTask.get(tid)} />
                          </>
                        }
                        metrics={flowTasks?.[tid]}
                      />
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
          <FlowFooter tasks={flowTasks} />
        </div>
      )}
    </section>
  );
}

// T-刀① item 1 — dispatch 短码 + copy;点短码 → 抽屉 Events tab 该 try 过滤。
// 行本身是 <button>(打开抽屉),内嵌动作用 span+stopPropagation 避免嵌套交互元素。
function DispatchCell({
  onOpenDispatch,
  taskId,
  tries,
}: {
  onOpenDispatch?: (taskId: string, trySel?: number) => void;
  taskId: string;
  tries?: DeliveryTaskTry[];
}) {
  const dispatchTry = latestDispatchTry(tries);
  if (!dispatchTry?.dispatch_id) {
    return <span className="dt-flow-dispatch is-null" title="dispatch: no data">—</span>;
  }
  const full = dispatchTry.dispatch_id;
  return (
    <span className="dt-flow-dispatch">
      <span
        className="dt-flow-dispatch-id"
        role="link"
        title={`${full} — open drawer Events tab for try#${dispatchTry.try}`}
        onClick={(event) => {
          event.stopPropagation();
          onOpenDispatch?.(taskId, dispatchTry.try);
        }}
      >
        {shortDispatch(full)}
      </span>
      <span
        className="dt-copy-icon"
        role="button"
        aria-label="copy dispatch id"
        title={`copy ${full}`}
        onClick={(event) => {
          event.stopPropagation();
          copyText(full);
        }}
      >
        ⧉
      </span>
    </span>
  );
}

// T-刀① item 2 — seq 锚:无带参跨页导航机制 → click = copy 范围 + title 提示。
function SeqCell({ tries }: { tries?: DeliveryTaskTry[] }) {
  const range = taskSeqRange(tries);
  if (!range) return <span className="dt-flow-num is-null" title="seq: no data">—</span>;
  const text = `${range.first}..${range.last}`;
  return (
    <span
      className="dt-flow-num dt-flow-seq"
      role="button"
      title={`seq ${text} — click copies the range; paste into the Observability seq filter`}
      onClick={(event) => {
        event.stopPropagation();
        copyText(text);
      }}
    >
      {text}
    </span>
  );
}

function WhyCell({ why }: { why?: { kind: string; reason: string } }) {
  if (!why) return <span className="dt-flow-why is-null" title="why-not: no notification for this task">—</span>;
  return <span className="dt-flow-why" title={why.reason || why.kind}>{why.kind}</span>;
}

function p90(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * 0.9) - 1)];
}

// Footer aggregates over flow_metrics.tasks (hidden entirely without metrics).
function FlowFooter({ tasks }: { tasks?: Record<string, DeliveryFlowTaskMetrics> }) {
  const list = Object.values(tasks ?? {});
  if (!list.length) return null;
  const qWaits = list.map((m) => m?.queue_wait_seconds).filter((v): v is number => typeof v === "number");
  const firstResps = list.map((m) => m?.first_response_seconds).filter((v): v is number => typeof v === "number");
  let backedges = 0;
  let rework = 0;
  let total = 0;
  for (const m of list) {
    backedges += m?.backedge_count ?? 0;
    rework += Math.max(0, m?.rework_seconds ?? 0);
    total += Math.max(0, m?.wait_seconds ?? 0) + Math.max(0, m?.active_seconds ?? 0) + Math.max(0, m?.rework_seconds ?? 0);
  }
  const p90Wait = p90(qWaits);
  const p90Resp = p90(firstResps);
  return (
    <div className="dt-flow-footer" data-testid="dt-flow-footer">
      <span>p90 q-wait <strong>{p90Wait === null ? "—" : formatSeconds(p90Wait)}</strong></span>
      <span>p90 first-resp <strong>{p90Resp === null ? "—" : formatSeconds(p90Resp)}</strong></span>
      <span>Σ backedge <strong>{backedges}</strong></span>
      <span title="Σrework / Σ(wait+active+rework)">rework <strong>{total > 0 ? `${Math.round((rework / total) * 100)}%` : "—"}</strong></span>
    </div>
  );
}

// S-C grid 形态 — 行=task,列=try/round(取 task_lifecycle.tries 最大数),
// 格=try outcome 字符(✓/✗/◐/·),行尾 conv 迷你线;superseded 行格用 ▒。
function TasksTryGridSection({ onSelectTask, trace }: { onSelectTask?: (taskId: string) => void; trace: DeliveryTrace }) {
  const lifecycle = trace.task_lifecycle?.tasks ?? {};
  const nodes = trace.execution_graph?.nodes ?? [];
  const taskIds = nodes.map((node) => node.task_id);
  for (const tid of Object.keys(lifecycle)) {
    if (!taskIds.includes(tid)) taskIds.push(tid);
  }
  const maxTries = Math.max(1, ...Object.values(lifecycle).map((entry) => entry.tries.length));
  const columns = {
    gridTemplateColumns: `minmax(120px, 240px) repeat(${maxTries}, 26px) minmax(64px, max-content)`,
  };
  return (
    <section className="delivery-trace-section">
      <h3 className="section-title">Task Attempts</h3>
      {taskIds.length === 0 ? (
        <p className="muted">No tasks projected yet — grid fills in once task lifecycle events land.</p>
      ) : (
        <div className="dt-try-grid" data-testid="dt-try-grid">
          <div className="dt-try-row dt-try-head" style={columns} aria-hidden="true">
            <span className="dt-try-id">task</span>
            {Array.from({ length: maxTries }, (_, index) => (
              <span key={index} className="dt-try-cell">#{index + 1}</span>
            ))}
            <span>conv</span>
          </div>
          {taskIds.map((tid) => {
            const superseded = !!nodes.find((node) => node.task_id === tid)?.superseded;
            const tries = lifecycle[tid]?.tries ?? [];
            return (
              <button
                key={tid}
                type="button"
                className={`dt-try-row${superseded ? " is-superseded" : ""}`}
                style={columns}
                onClick={() => onSelectTask?.(tid)}
              >
                <span className="dt-try-id" title={tid}>{tid}</span>
                {Array.from({ length: maxTries }, (_, index) => {
                  const tryItem = tries[index];
                  if (superseded) {
                    return <span key={index} className="dt-try-cell is-null" title="superseded">▒</span>;
                  }
                  if (!tryItem) {
                    return <span key={index} className="dt-try-cell is-null" title="no try">·</span>;
                  }
                  const glyph = tryItem.outcome === "done" ? "✓"
                    : tryItem.outcome === "in_flight" ? "◐" : "✗";
                  const cls = tryItem.outcome === "done" ? "is-pass"
                    : tryItem.outcome === "in_flight" ? "is-flight" : "is-fail";
                  return (
                    <span
                      key={index}
                      className={`dt-try-cell ${cls}`}
                      title={`try#${tryItem.try} ${tryItem.outcome}${tryItem.rework_kind ? ` · ${tryItem.rework_kind}` : ""}`}
                    >
                      {glyph}
                    </span>
                  );
                })}
                <ConvSpark rounds={trace.flow_metrics?.tasks?.[tid]?.convergence} />
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

// 2026-06-10 slice 1 — per-task flow columns: [lead: dispatch|seq|why] q-wait |
// first-resp | active | SegBar(wait/active/rework) | backedge | conv.
// Nulls render as muted "—".
function FlowCells({ lead, metrics }: { lead?: ReactNode; metrics?: DeliveryFlowTaskMetrics }) {
  return (
    <span className="dt-flow-cols" data-testid="dt-flow-cells">
      {lead}
      <FlowNum label="q-wait" value={metrics?.queue_wait_seconds} />
      <FlowNum label="first-resp" value={metrics?.first_response_seconds} />
      <FlowNum label="active" value={metrics?.active_seconds} />
      <SegBar
        mini
        wait={metrics?.wait_seconds}
        active={metrics?.active_seconds}
        rework={metrics?.rework_seconds}
      />
      <FlowNum label="backedge" value={metrics?.backedge_count} raw />
      <ConvSpark rounds={metrics?.convergence} />
    </span>
  );
}

function FlowNum({ label, value, raw }: { label: string; value?: number | null; raw?: boolean }) {
  const isNull = value === null || value === undefined;
  const text = isNull ? "—" : raw ? String(value) : formatSeconds(value);
  return (
    <span className={`dt-flow-num${isNull ? " is-null" : ""}`} title={`${label}: ${text}`}>
      {text}
    </span>
  );
}

// Convergence sparkline: per round ratio = passed/(passed+failed) → ▂▅▇
// ("·" when a round saw no gate outcome). Flat/declining for ≥2 consecutive
// rounds appends a warn-colored ✗.
function ConvSpark({ rounds }: { rounds?: DeliveryFlowConvergenceRound[] }) {
  if (!rounds?.length) {
    return <span className="dt-flow-conv is-null" title="conv: no rounds">—</span>;
  }
  const ratios = rounds.map((round) => {
    const total = (round.passed ?? 0) + (round.failed ?? 0);
    return total > 0 ? (round.passed ?? 0) / total : null;
  });
  const known = ratios.filter((ratio): ratio is number => ratio !== null);
  const last = known[known.length - 1];
  const prev = known[known.length - 2];
  // Stalled = pass rate flat/declining across the latest two rounds and not
  // already fully passing (flat at 100% is converged, not stalled).
  const stalled = known.length >= 2 && last <= prev && last < 1;
  const glyph = (ratio: number | null) =>
    ratio === null ? "·" : ratio < 1 / 3 ? "▂" : ratio < 2 / 3 ? "▅" : "▇";
  const title = `conv: ${rounds.map((round) => `r${round.round} ${round.passed}✓/${round.failed}✗`).join(" · ")}`;
  return (
    <span className="dt-flow-conv" title={title}>
      {ratios.map(glyph).join("")}
      {stalled && <span className="conv-stall" title="convergence stalled/declining ≥2 rounds">✗</span>}
    </span>
  );
}

function DriftSection({ trace }: { trace: DeliveryTrace }) {
  return (
    <section className="delivery-trace-section">
      <h3 className="section-title">Drift</h3>
      {trace.drift_report.items.length === 0 ? (
        <p className="muted">No drift.</p>
      ) : (
        <ul className="delivery-trace-drift">
          {trace.drift_report.items.map((item, idx) => (
            <li key={idx}>
              <span className={`badge badge-${item.severity === "error" ? "err" : item.severity === "warning" ? "warn" : "info"}`}>{item.severity}</span>
              <code className="dt-node-id">{item.task_id}</code>
              <span className="dt-drift-kind">{item.kind}</span>
              <span className="dt-drift-msg">{item.message}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
