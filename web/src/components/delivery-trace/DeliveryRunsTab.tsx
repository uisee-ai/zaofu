// DeliveryRunsTab (R-刀) — GitHub Actions job 风格的 attempt 列表。
// 四 tab 职责: Run Graph=现在(历史压缩) · Tasks=对象 ·
// **Runs=每一次尝试(不可变记录)** · Trace=时间真相(统一时间轴)。
// 每个 run group 一行 run:{stage}:#{n}(状态 glyph + 起止 + 时长 + children n/m),
// superseded run 标 ▒ + sunk 结账;展开 = children 子行 + barrier/aggregate +
// artifacts + 三锚(dispatch ⧉ / seq[a..b] / view in Trace ▸)。
import { useMemo, useState } from "react";

import type { DeliveryTrace } from "../../api/types";
import { formatSeconds } from "../common/SegBar";
import {
  ATTEMPT_GLYPH,
  buildAttemptRows,
  summarizeAttempts,
} from "./DeliveryRunsModel";
import type { AttemptChildRow, AttemptRow } from "./DeliveryRunsModel";
import {
  clockLabel,
  copyText,
  dtTone,
  formatDuration,
  shortDispatch,
} from "./DeliveryTraceViewUtils";
import type { TraceFocus } from "./DeliveryTraceViewUtils";

interface DeliveryRunsTabProps {
  trace: DeliveryTrace;
  onViewInTrace?: (focus: TraceFocus) => void;
}

export function DeliveryRunsTab({ trace, onViewInTrace }: DeliveryRunsTabProps) {
  const rows = useMemo(() => buildAttemptRows(trace), [trace]);
  const [stageFilter, setStageFilter] = useState("");
  // Failed / running attempts open by default (GitHub Actions failed-job UX).
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const stages = useMemo(() => [...new Set(rows.map((row) => row.stage))], [rows]);
  const visible = stageFilter ? rows.filter((row) => row.stage === stageFilter) : rows;
  const summary = useMemo(() => summarizeAttempts(rows), [rows]);

  if (!rows.length) {
    return <div className="delivery-tab-empty" data-testid="delivery-runs-tab">No run groups projected.</div>;
  }

  return (
    <div className="dt-runs-panel" data-testid="delivery-runs-tab">
      <div className="dt-runs-summary" data-testid="dt-runs-summary">
        <span className="dt-runs-sigma" title={`completed ${summary.success} of ${summary.total} attempts`}>
          Σ success {summary.success}/{summary.total}
          {" · rerun "}{summary.rerun}
          {" · sunk "}{summary.sunkMs > 0 ? formatDuration(summary.sunkMs) : "0s"}
        </span>
        <select
          className="dt-runs-stage-filter"
          aria-label="Filter runs by stage"
          value={stageFilter}
          onChange={(event) => setStageFilter(event.target.value)}
        >
          <option value="">all stages ({rows.length})</option>
          {stages.map((stage) => (
            <option key={stage} value={stage}>{stage}</option>
          ))}
        </select>
      </div>
      <div className="dt-attempt-list">
        {visible.map((row) => (
          <AttemptItem
            key={row.group.group_id}
            expanded={expanded[row.group.group_id] ?? ["failed", "running"].includes(row.statusKind)}
            onToggle={() =>
              setExpanded((prev) => ({
                ...prev,
                [row.group.group_id]: !(prev[row.group.group_id] ?? ["failed", "running"].includes(row.statusKind)),
              }))}
            onViewInTrace={onViewInTrace}
            row={row}
          />
        ))}
        {!visible.length && <p className="muted">No runs in this stage.</p>}
      </div>
    </div>
  );
}

function AttemptItem({
  expanded,
  onToggle,
  onViewInTrace,
  row,
}: {
  expanded: boolean;
  onToggle: () => void;
  onViewInTrace?: (focus: TraceFocus) => void;
  row: AttemptRow;
}) {
  const sunk = row.superseded;
  const timingLabel = attemptTimingLabel(row);
  const timingTitle = attemptTimingTitle(row);
  return (
    <article className={`dt-attempt is-${row.statusKind}`} data-testid="dt-attempt-row">
      <button type="button" className="dt-attempt-head" aria-expanded={expanded} onClick={onToggle}>
        <span className="dt-attempt-caret" aria-hidden="true">{expanded ? "▾" : "▸"}</span>
        <span className={`dt-attempt-glyph kind-${row.statusKind}`} aria-hidden="true">
          {ATTEMPT_GLYPH[row.statusKind]}
        </span>
        <span className="dt-attempt-name" title={`${row.group.group_id} · ${row.group.label || row.stage}`}>
          {row.name}
        </span>
        {sunk && (
          <span
            className="dt-attempt-sunk"
            title={`attempt superseded by replan${sunk.replanVersion != null ? ` v${sunk.replanVersion}` : ""}; its spend is sunk`}
          >
            ▒ superseded{sunk.replanVersion != null ? ` (replan v${sunk.replanVersion})` : ""}
            {" · sunk "}{sunk.sunkMs != null ? formatDuration(sunk.sunkMs) : "—"}
            {sunk.tokensIn != null || sunk.tokensOut != null
              ? ` · ${sunk.tokensIn ?? 0}/${sunk.tokensOut ?? 0} tok`
              : ""}
            {sunk.costUsd != null ? ` · $${sunk.costUsd.toFixed(2)}` : ""}
          </span>
        )}
        {timingLabel ? (
          <span className="dt-attempt-times" title={timingTitle}>
            {timingLabel}
          </span>
        ) : null}
        {row.childrenTotal > 0 ? (
          <span
            className="dt-attempt-children-count"
            title={`${row.childrenDone} of ${row.childrenTotal} children done`}
          >
            {row.childrenDone}/{row.childrenTotal}
          </span>
        ) : null}
        <span className={`badge badge-${dtTone(row.group.status)}`}>{row.group.status}</span>
      </button>
      {expanded && (
        <div className="dt-attempt-body">
          <AttemptAnchors onViewInTrace={onViewInTrace} row={row} />
          <div className="dt-attempt-children">
            {row.children.map((child) => <ChildRow key={child.key} child={child} />)}
            {!row.children.length && <span className="muted">No child lanes recorded for this attempt.</span>}
          </div>
          {row.aggregate && (
            <div className="dt-attempt-aggregate" data-testid="dt-attempt-aggregate">
              <span className="dt-attempt-agg-tag">barrier/aggregate</span>
              <span>wait {row.aggregate.waitMs != null ? formatDuration(row.aggregate.waitMs) : "—"}</span>
              <span>{row.aggregate.tasks} tasks</span>
              <span>{row.aggregate.events} events</span>
            </div>
          )}
          <div className="dt-attempt-artifacts">
            {row.artifacts.map((ref) => <code key={ref} title={ref}>{ref}</code>)}
            {!row.artifacts.length && <span className="muted">No artifact / evidence refs.</span>}
          </div>
        </div>
      )}
    </article>
  );
}

function attemptTimingLabel(row: AttemptRow): string {
  const parts: string[] = [];
  if (row.startedAt || row.endedAt) {
    parts.push(`${row.startedAt ? clockLabel(row.startedAt) : "?"} → ${row.endedAt ? clockLabel(row.endedAt) : "running"}`);
  }
  if (row.durationMs != null) {
    parts.push(formatDuration(row.durationMs));
  }
  return parts.join(" · ");
}

function attemptTimingTitle(row: AttemptRow): string {
  const parts = [
    row.startedAt ? `started ${row.startedAt}` : "",
    row.endedAt ? `ended ${row.endedAt}` : "",
    row.durationMs != null ? `duration ${formatDuration(row.durationMs)}` : "",
  ].filter(Boolean);
  return parts.join(" · ") || "no timing recorded";
}

// 三锚 — dispatch ⧉ (copy full id) · seq[a..b] (copy, Observability filter) ·
// view in Trace ▸ (switch tab with focusWindow + focusRunId).
function AttemptAnchors({
  onViewInTrace,
  row,
}: {
  onViewInTrace?: (focus: TraceFocus) => void;
  row: AttemptRow;
}) {
  const seqText = row.seqRange ? `${row.seqRange.first}..${row.seqRange.last}` : null;
  return (
    <div className="dt-attempt-anchors" data-testid="dt-attempt-anchors">
      {row.primaryDispatchId ? (
        <button
          type="button"
          className="dt-anchor"
          title={`copy ${row.primaryDispatchId}`}
          onClick={() => copyText(row.primaryDispatchId!)}
        >
          dispatch-{shortDispatch(row.primaryDispatchId)} ⧉
        </button>
      ) : (
        <span className="dt-anchor is-null" title="no dispatch id on this attempt">dispatch —</span>
      )}
      {seqText ? (
        <button
          type="button"
          className="dt-anchor dt-anchor-seq"
          title={`seq ${seqText} — click copies the range; paste into the Observability seq filter`}
          onClick={() => copyText(seqText)}
        >
          seq[{seqText}]
        </button>
      ) : (
        <span className="dt-anchor is-null" title="no event:{seq} spans linked to this attempt">seq —</span>
      )}
      <button
        type="button"
        className="dt-anchor dt-anchor-trace"
        data-testid="dt-anchor-trace"
        title="open Trace tab scrolled to this attempt's time window"
        onClick={() => onViewInTrace?.(row.focus)}
      >
        view in Trace ▸
      </button>
    </div>
  );
}

function ChildRow({ child }: { child: AttemptChildRow }) {
  return (
    <div className="dt-attempt-child" data-testid="dt-attempt-child">
      <span className={`workflow-status-dot status-${dtTone(child.status)}`} />
      <span className="dt-child-task" title={child.taskLabel}>{child.taskLabel}</span>
      <span className="dt-child-lane" title={`lane ${child.lane}`}>{child.lane}</span>
      <span className="dt-child-try">{child.tryNo != null ? `try#${child.tryNo}` : "—"}</span>
      <span className={`badge badge-${dtTone(child.status)}`}>{child.status}</span>
      <span className="dt-child-dur">{child.durationMs != null ? formatSeconds(child.durationMs / 1000) : "—"}</span>
      {child.dispatchId ? (
        <button
          type="button"
          className="dt-anchor dt-child-dispatch"
          title={`copy ${child.dispatchId}`}
          onClick={() => copyText(child.dispatchId!)}
        >
          {shortDispatch(child.dispatchId)} ⧉
        </button>
      ) : (
        <span className="dt-child-dispatch is-null">—</span>
      )}
      <span className="dt-child-gates">
        {child.gates.map((gate, index) => (
          <span
            key={index}
            className={gate.passed ? "dt-gate-pass" : "dt-gate-fail"}
            title={`${gate.type} ${gate.passed ? "passed" : "failed"}`}
          >
            {gate.passed ? "✓" : "✗"}
          </span>
        ))}
      </span>
      {child.note && <span className="dt-child-note" title={child.note}>{child.note}</span>}
    </div>
  );
}
