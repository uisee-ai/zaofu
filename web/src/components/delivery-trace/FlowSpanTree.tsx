// FlowSpanTree (T-刀④ 树瀑布合体) — Trace=时间真相(统一时间轴)。
// 左列层级树: feature → phase → task → try(task_lifecycle.tries)→ event 叶
// (spans 挂 task/try,默认折叠到 try 层);右列瀑布条共享 feature 时间轴
// (TraceWaterfallModel)。原底部 LIFELINE 条移除 — 其时间点升级为瀑布顶部的
// 轴刻度行。联动: focusWindow/focusRunId(来自 Runs 三锚)滚动+高亮该窗内行;
// causalIds(Run Graph ⛓ 回放)高亮链上 event 叶。纯 CSS 定位,无图表库。
import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import type { DeliveryTrace } from "../../api/types";
import { SegBar } from "../common/SegBar";
import { runDispatchIds } from "./DeliveryRunsModel";
import { clockLabel, dtTone, parseTs } from "./DeliveryTraceViewUtils";
import type { TraceFocus } from "./DeliveryTraceViewUtils";
import { WF_LEAF_CAP, buildWaterfallModel, wfPct } from "./TraceWaterfallModel";
import type { WfDomain, WfLeaf, WfTask, WfTry } from "./TraceWaterfallModel";

interface FlowSpanTreeProps {
  trace: DeliveryTrace;
  focus?: TraceFocus | null;
  causalIds?: Set<string> | null;
  selectedSpanId?: string;
  onSelectSpan?: (spanId: string) => void;
}

interface FocusInfo {
  startMs: number | null;
  endMs: number | null;
  dispatch: Set<string>;
  tasks: Set<string>;
}

function focusInfoFor(trace: DeliveryTrace, focus?: TraceFocus | null): FocusInfo | null {
  if (!focus) return null;
  const group = (trace.run_groups ?? []).find((item) => item.group_id === focus.focusRunId);
  return {
    startMs: parseTs(focus.focusWindow.start_ts),
    endMs: parseTs(focus.focusWindow.end_ts),
    dispatch: new Set(group ? runDispatchIds(trace, group) : []),
    tasks: new Set(group?.task_ids ?? []),
  };
}

function overlapsFocus(info: FocusInfo | null, startMs: number | null, endMs: number | null): boolean {
  if (!info || (info.startMs === null && info.endMs === null)) return false;
  const start = startMs ?? endMs;
  const end = endMs ?? startMs;
  if (start === null || end === null) return false;
  const fs = info.startMs ?? info.endMs!;
  const fe = info.endMs ?? info.startMs!;
  return start <= fe && end >= fs;
}

export function FlowSpanTree({ trace, focus, causalIds, selectedSpanId, onSelectSpan }: FlowSpanTreeProps) {
  const model = useMemo(() => buildWaterfallModel(trace), [trace]);
  const focusInfo = useMemo(() => focusInfoFor(trace, focus), [trace, focus]);
  // Phases open by default (tree unfolds down to the try layer; leaves fold).
  const [closedPhases, setClosedPhases] = useState<Record<string, boolean>>({});
  // Try rows owning the focused run's dispatches start open (component mounts
  // fresh on each Runs → Trace switch, so the initializer sees current focus).
  const [openTries, setOpenTries] = useState<Record<string, boolean>>(() => {
    if (!focusInfo) return {};
    const open: Record<string, boolean> = {};
    for (const phase of model.phases) {
      for (const task of phase.tasks) {
        for (const tryItem of task.tries) {
          if ((tryItem.dispatchId && focusInfo.dispatch.has(tryItem.dispatchId))
            || overlapsFocus(focusInfo, tryItem.startMs, tryItem.endMs)) {
            open[tryItem.key] = true;
          }
        }
      }
    }
    return open;
  });
  const [showUnassigned, setShowUnassigned] = useState(false);

  useEffect(() => {
    if (!focus || typeof document === "undefined") return;
    document.querySelector(".wf-row.is-focus")?.scrollIntoView({ block: "center" });
  }, [focus]);

  const { domain } = model;
  const usage = trace.trace?.usage_summary;
  const isEmpty = model.phases.length === 0 && model.unassigned.length === 0;

  return (
    <section className="dt-span-tree-panel wf-panel" data-testid="dt-flow-span-tree">
      <div className="inline-heading">
        <h3 className="section-title">Delivery Spans</h3>
        <span className="muted">
          feature → phase → task → try → event · {model.leafTotal} spans
          {usage ? ` · ${String(usage.input_tokens ?? 0)} in / ${String(usage.output_tokens ?? 0)} out` : ""}
        </span>
      </div>
      <WfAxis domain={domain} model={model} />
      <WfRow
        className="wf-feature"
        label={(
          <>
            <span className="dt-tree-label" title={trace.feature_id}>{trace.feature_id}</span>
            {trace.workflow_archetype && (
              <span className="badge dt-archetype-badge" title={`workflow archetype: ${trace.workflow_archetype}`}>
                [{trace.workflow_archetype}]
              </span>
            )}
            <span className={`badge badge-${dtTone(trace.status)}`}>{trace.status}</span>
          </>
        )}
        bar={domain ? <WfBar domain={domain} startMs={domain.min} endMs={domain.max} className="wf-bar-feature" /> : <WfNoBar />}
      />
      {isEmpty && (
        <p className="dt-tree-empty muted">No phase, task, or span projection yet — rows appear after task-map/kanban evidence lands.</p>
      )}
      {model.phases.map((phase) => {
        const open = !closedPhases[phase.id];
        const tasksTimes = phase.tasks.flatMap((task) => [task.startMs, task.endMs]).filter((v): v is number => v !== null);
        return (
          <div key={phase.id} className="wf-phase-block">
            <WfRow
              className={`wf-phase${overlapsFocus(focusInfo, Math.min(...tasksTimes), Math.max(...tasksTimes)) && tasksTimes.length ? " is-focus" : ""}`}
              onClick={() => setClosedPhases((prev) => ({ ...prev, [phase.id]: open }))}
              ariaExpanded={open}
              label={(
                <>
                  <span className="dt-tree-caret">{open ? "▾" : "▸"}</span>
                  <span className="dt-tree-label" title={phase.label}>{phase.label}</span>
                  <span className={`badge badge-${dtTone(phase.status)}`}>{phase.status}</span>
                  <span className="wf-sigma muted">task {phase.doneCount}/{phase.taskCount}</span>
                </>
              )}
              bar={domain && tasksTimes.length
                ? <WfBar domain={domain} startMs={Math.min(...tasksTimes)} endMs={Math.max(...tasksTimes)} className="wf-bar-phase" />
                : <WfNoBar />}
            />
            {open && phase.tasks.map((task) => (
              <TaskRows
                key={task.taskId}
                causalIds={causalIds}
                domain={domain}
                focusInfo={focusInfo}
                onSelectSpan={onSelectSpan}
                openTries={openTries}
                selectedSpanId={selectedSpanId}
                setOpenTries={setOpenTries}
                task={task}
              />
            ))}
            {open && !phase.tasks.length && (
              <p className="dt-tree-empty dt-tree-empty-indent muted">No tasks mapped to this phase yet.</p>
            )}
          </div>
        );
      })}
      {model.unassigned.length > 0 && (
        <>
          <WfRow
            className="wf-try wf-unassigned"
            onClick={() => setShowUnassigned((value) => !value)}
            ariaExpanded={showUnassigned}
            label={(
              <>
                <span className="dt-tree-caret">{showUnassigned ? "▾" : "▸"}</span>
                <span className="dt-tree-label">ungrouped events ({model.unassigned.length})</span>
              </>
            )}
            bar={<WfNoBar />}
          />
          {showUnassigned && <LeafRows causalIds={causalIds} domain={domain} focusInfo={focusInfo} leaves={model.unassigned} onSelectSpan={onSelectSpan} selectedSpanId={selectedSpanId} />}
        </>
      )}
    </section>
  );
}

// 轴刻度行 — LIFELINE 点按时间 pct 定位; domain 缺失时显式降级一句话。
function WfAxis({ domain, model }: { domain: WfDomain | null; model: ReturnType<typeof buildWaterfallModel> }) {
  if (!domain) {
    return <p className="dt-tree-empty muted" data-testid="wf-axis-empty">No span timestamps yet — waterfall bars are hidden until timing evidence lands.</p>;
  }
  return (
    <div className="wf-row wf-axis-row" data-testid="wf-axis" aria-label="Trace time axis">
      <span className="wf-row-label wf-axis-label">
        <span className="dt-lifeline-tag">AXIS</span>
        <span className="wf-axis-clock">{clockLabel(new Date(domain.min).toISOString())} → {clockLabel(new Date(domain.max).toISOString())}</span>
      </span>
      <span className="wf-track wf-axis-track">
        {model.ticks.map((tick, index) => (
          <span
            key={index}
            className={`wf-axis-tick${tick.bad ? " is-bad" : ""}`}
            style={{ left: `${tick.pct}%` }}
            title={`${tick.kind} · ${tick.clock} — ${tick.title}`}
          >
            <i aria-hidden="true">{tick.bad ? "✗" : "●"}</i>
            <small>{tick.kind}</small>
          </span>
        ))}
      </span>
    </div>
  );
}

function WfRow({ ariaExpanded, bar, className, label, onClick }: {
  ariaExpanded?: boolean;
  bar: ReactNode;
  className: string;
  label: ReactNode;
  onClick?: () => void;
}) {
  const body = (
    <>
      <span className="wf-row-label">{label}</span>
      {bar}
    </>
  );
  if (onClick) {
    return (
      <button type="button" className={`wf-row ${className}`} aria-expanded={ariaExpanded} onClick={onClick}>
        {body}
      </button>
    );
  }
  return <div className={`wf-row ${className}`}>{body}</div>;
}

function WfNoBar() {
  return <span className="wf-track wf-track-empty" aria-hidden="true" />;
}

function WfBar({ children, className, domain, endMs, startMs }: {
  children?: ReactNode;
  className: string;
  domain: WfDomain;
  endMs: number | null;
  startMs: number | null;
}) {
  if (startMs === null && endMs === null) return <WfNoBar />;
  const left = wfPct(domain, startMs ?? endMs!);
  const right = endMs !== null ? wfPct(domain, endMs) : left;
  const width = Math.max(0.6, right - left);
  return (
    <span className="wf-track">
      <span className={`wf-bar ${className}`} style={{ left: `${left}%`, width: `${width}%` }}>{children}</span>
    </span>
  );
}

function TaskRows({ causalIds, domain, focusInfo, onSelectSpan, openTries, selectedSpanId, setOpenTries, task }: {
  causalIds?: Set<string> | null;
  domain: WfDomain | null;
  focusInfo: FocusInfo | null;
  onSelectSpan?: (spanId: string) => void;
  openTries: Record<string, boolean>;
  selectedSpanId?: string;
  setOpenTries: (updater: (prev: Record<string, boolean>) => Record<string, boolean>) => void;
  task: WfTask;
}) {
  const taskFocus = !!focusInfo && (focusInfo.tasks.has(task.taskId) || overlapsFocus(focusInfo, task.startMs, task.endMs));
  const windowSec = task.startMs !== null && task.endMs !== null ? Math.max(0, (task.endMs - task.startMs) / 1000) : null;
  return (
    <>
      <WfRow
        className={`wf-task${taskFocus ? " is-focus" : ""}`}
        label={(
          <>
            <span className="dt-tree-label mono" title={task.taskId}>{task.taskId}</span>
            <span className={`badge badge-${dtTone(task.status)}`}>{task.status}</span>
            <span className="wf-times" title={`started ${task.startMs !== null ? new Date(task.startMs).toISOString() : "—"} · completed ${task.endMs !== null ? new Date(task.endMs).toISOString() : "—"}`}>
              {clockLabel(task.startMs !== null ? new Date(task.startMs).toISOString() : null)} → {clockLabel(task.endMs !== null ? new Date(task.endMs).toISOString() : null)}
            </span>
          </>
        )}
        bar={domain
          ? (
            <WfBar domain={domain} startMs={task.startMs} endMs={task.endMs} className="wf-bar-seg">
              <SegBar wait={task.seg.wait} active={task.seg.active} rework={task.seg.rework} totalHint={windowSec} />
            </WfBar>
          )
          : <WfNoBar />}
      />
      {task.tries.map((tryItem) => (
        <TryRows
          key={tryItem.key}
          causalIds={causalIds}
          domain={domain}
          focusInfo={focusInfo}
          onSelectSpan={onSelectSpan}
          open={!!openTries[tryItem.key]}
          selectedSpanId={selectedSpanId}
          toggle={() => setOpenTries((prev) => ({ ...prev, [tryItem.key]: !prev[tryItem.key] }))}
          tryItem={tryItem}
        />
      ))}
    </>
  );
}

function TryRows({ causalIds, domain, focusInfo, onSelectSpan, open, selectedSpanId, toggle, tryItem }: {
  causalIds?: Set<string> | null;
  domain: WfDomain | null;
  focusInfo: FocusInfo | null;
  onSelectSpan?: (spanId: string) => void;
  open: boolean;
  selectedSpanId?: string;
  toggle: () => void;
  tryItem: WfTry;
}) {
  const tryFocus = !!focusInfo && ((!!tryItem.dispatchId && focusInfo.dispatch.has(tryItem.dispatchId))
    || overlapsFocus(focusInfo, tryItem.startMs, tryItem.endMs));
  const windowSec = tryItem.startMs !== null && tryItem.endMs !== null
    ? Math.max(0, (tryItem.endMs - tryItem.startMs) / 1000)
    : null;
  const wait = Math.min(tryItem.firstResponseSeconds ?? 0, windowSec ?? Number.MAX_SAFE_INTEGER);
  const remainder = windowSec !== null ? Math.max(0, windowSec - wait) : null;
  return (
    <>
      <WfRow
        className={`wf-try${tryFocus ? " is-focus" : ""}`}
        onClick={toggle}
        ariaExpanded={open}
        label={(
          <>
            <span className="dt-tree-caret">{open ? "▾" : "▸"}</span>
            <span className="dt-tree-label" title={tryItem.synthetic ? "spans without a recorded try" : `try#${tryItem.tryNo}`}>
              {tryItem.synthetic ? "events" : `try#${tryItem.tryNo}`}
            </span>
            <span className={`badge badge-${dtTone(tryItem.outcome)}`}>{tryItem.outcome}</span>
            {tryItem.reworkKind && <span className="badge badge-warn">{tryItem.reworkKind}</span>}
            <span className="wf-sigma muted">{tryItem.leaves.length} ev</span>
          </>
        )}
        bar={domain
          ? (
            <WfBar domain={domain} startMs={tryItem.startMs} endMs={tryItem.endMs} className="wf-bar-seg">
              <SegBar
                wait={wait}
                active={tryItem.reworkKind ? 0 : remainder}
                rework={tryItem.reworkKind ? remainder : 0}
                totalHint={windowSec}
              />
            </WfBar>
          )
          : <WfNoBar />}
      />
      {open && <LeafRows causalIds={causalIds} domain={domain} focusInfo={focusInfo} leaves={tryItem.leaves} onSelectSpan={onSelectSpan} selectedSpanId={selectedSpanId} />}
    </>
  );
}

function LeafRows({ causalIds, domain, focusInfo, leaves, onSelectSpan, selectedSpanId }: {
  causalIds?: Set<string> | null;
  domain: WfDomain | null;
  focusInfo: FocusInfo | null;
  leaves: WfLeaf[];
  onSelectSpan?: (spanId: string) => void;
  selectedSpanId?: string;
}) {
  const shown = leaves.slice(0, WF_LEAF_CAP);
  const hidden = leaves.length - shown.length;
  return (
    <>
      {shown.map((leaf) => {
        const span = leaf.span;
        const causal = !!causalIds && (causalIds.has(span.span_id)
          || (span.raw_event_refs ?? []).some((ref) => causalIds.has(ref)));
        const leafFocus = !!focusInfo && ((!!span.run_id && focusInfo.dispatch.has(span.run_id))
          || overlapsFocus(focusInfo, leaf.startMs, leaf.endMs));
        return (
          <WfRow
            key={span.span_id}
            className={`wf-leaf${leafFocus ? " is-focus" : ""}${causal ? " is-causal" : ""}${selectedSpanId === span.span_id ? " active" : ""}`}
            onClick={() => onSelectSpan?.(span.span_id)}
            label={(
              <>
                <span className="wf-leaf-seq mono" title={span.span_id}>{span.span_id}</span>
                <span className="wf-leaf-meta" title={span.role || span.backend || span.run_id || "event"}>
                  {span.role || span.backend || span.run_id || "event"}
                </span>
                <span className={`badge badge-${dtTone(span.status)}`}>{span.status}</span>
              </>
            )}
            bar={domain
              ? <WfBar domain={domain} startMs={leaf.startMs} endMs={leaf.endMs} className={`wf-bar-leaf tone-${dtTone(span.status)}`} />
              : <WfNoBar />}
          />
        );
      })}
      {hidden > 0 && <p className="dt-tree-empty dt-tree-empty-indent muted" data-testid="wf-leaf-more">+{hidden} more events (render-capped)</p>}
      {!leaves.length && <p className="dt-tree-empty dt-tree-empty-indent muted">No spans landed in this window.</p>}
    </>
  );
}
