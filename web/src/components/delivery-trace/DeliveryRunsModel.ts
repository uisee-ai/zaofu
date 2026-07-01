// DeliveryRunsModel (R-刀) — pure projection helpers for the Runs tab.
// Runs = 每一次尝试(不可变记录): each run_group becomes one attempt row
// `run:{stage}:#{n}` (same stage ordered by started_at). Inputs are existing
// payload fields only — run_groups (group_id/source_event_ids/duration_ms/
// started_at/children/metrics), task_map_history (superseded attribution),
// task_lifecycle tries (dispatch_id ↔ children), spans (run_id = dispatch_id,
// span_id = `event:{seq}`). Zero backend changes; missing fields degrade.
import type {
  DeliveryRunGroup,
  DeliveryRunTraceSpan,
  DeliveryTaskTry,
  DeliveryTrace,
} from "../../api/types";
import { parseSpanSeq, parseTs } from "./DeliveryTraceViewUtils";
import type { TraceFocus } from "./DeliveryTraceViewUtils";

export type AttemptStatusKind =
  | "completed" | "failed" | "superseded" | "timed_out" | "running" | "pending";

// Status glyph language: ● completed / ✖ failed / ▒ superseded /
// ▤ timed_out|cancelled (+ ◐ running, ○ pending as graceful extras).
export const ATTEMPT_GLYPH: Record<AttemptStatusKind, string> = {
  completed: "●", failed: "✖", superseded: "▒", timed_out: "▤", running: "◐", pending: "○",
};

const DONE_STATUSES = ["done", "passed", "ok", "completed", "success", "succeeded"];
const FAIL_STATUSES = ["failed", "error", "rejected"];

export interface AttemptChildRow {
  key: string;
  taskLabel: string;
  lane: string;
  tryNo: number | null;
  status: string;
  durationMs: number | null;
  dispatchId: string | null;
  gates: Array<{ type: string; passed: boolean }>;
  note: string;
}

export interface AttemptSunk {
  replanVersion: number | null;
  sunkMs: number | null;
  tokensIn: number | null;
  tokensOut: number | null;
  costUsd: number | null;
}

export interface AttemptRow {
  group: DeliveryRunGroup;
  stage: string;
  attempt: number;
  name: string; // run:{stage}:#{n}
  statusKind: AttemptStatusKind;
  startedAt: string | null;
  endedAt: string | null;
  durationMs: number | null;
  childrenDone: number;
  childrenTotal: number;
  superseded: AttemptSunk | null;
  seqRange: { first: number; last: number } | null;
  primaryDispatchId: string | null;
  children: AttemptChildRow[];
  aggregate: { waitMs: number | null; tasks: number; events: number } | null;
  artifacts: string[];
  focus: TraceFocus;
}

export interface RunsSummary {
  success: number;
  total: number;
  rerun: number;
  sunkMs: number;
}

export function attemptStatusKind(status: string, superseded: boolean): AttemptStatusKind {
  if (superseded) return "superseded";
  const value = (status || "").toLowerCase();
  if (/timed_out|timeout|cancelled|canceled/.test(value)) return "timed_out";
  if (FAIL_STATUSES.includes(value)) return "failed";
  if (DONE_STATUSES.includes(value)) return "completed";
  if (["running", "in_progress", "active"].includes(value)) return "running";
  return "pending";
}

function str(value: unknown): string {
  return value === null || value === undefined ? "" : String(value);
}

function num(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() && !Number.isNaN(Number(value))) return Number(value);
  return null;
}

// Children carry loosely-typed dispatch references: run_id / dispatch_id / child_id.
function childDispatchId(child: Record<string, unknown>): string | null {
  return str(child.run_id) || str(child.dispatch_id) || null;
}

function lifecycleTries(trace: DeliveryTrace, taskId: string): DeliveryTaskTry[] {
  return trace.task_lifecycle?.tasks?.[taskId]?.tries ?? [];
}

// dispatch ids reachable from one run group: children refs + lifecycle tries
// of the group's task_ids (spans join on run_id = dispatch_id).
export function runDispatchIds(trace: DeliveryTrace, group: DeliveryRunGroup): string[] {
  const ids = new Set<string>();
  for (const child of group.children) {
    const id = childDispatchId(child);
    if (id) ids.add(id);
  }
  for (const taskId of group.task_ids) {
    for (const tryItem of lifecycleTries(trace, taskId)) {
      if (tryItem.dispatch_id) ids.add(tryItem.dispatch_id);
    }
  }
  return [...ids];
}

export function runSpans(
  spans: DeliveryRunTraceSpan[],
  group: DeliveryRunGroup,
  dispatchIds: string[],
): DeliveryRunTraceSpan[] {
  const dispatch = new Set(dispatchIds);
  const tasks = new Set(group.task_ids);
  const byDispatch = spans.filter((span) => span.run_id && dispatch.has(span.run_id));
  if (byDispatch.length) return byDispatch;
  return spans.filter((span) => span.task_id && tasks.has(span.task_id));
}

function seqRange(spans: DeliveryRunTraceSpan[]): { first: number; last: number } | null {
  let first: number | null = null;
  let last: number | null = null;
  for (const span of spans) {
    const seq = parseSpanSeq(span.span_id);
    if (seq === null) continue;
    if (first === null || seq < first) first = seq;
    if (last === null || seq > last) last = seq;
  }
  return first === null || last === null ? null : { first, last };
}

function groupDurationMs(group: DeliveryRunGroup): number | null {
  if (group.duration_ms != null) return group.duration_ms;
  const start = parseTs(group.started_at);
  const end = parseTs(group.ended_at);
  return start !== null && end !== null && end >= start ? end - start : null;
}

// Superseded attribution: every task of the run maps to a superseded
// execution-graph node (replan dropped them) → the whole attempt is sunk.
function supersededInfo(trace: DeliveryTrace, group: DeliveryRunGroup): AttemptSunk | null {
  const history = trace.task_map_history ?? [];
  const nodes = trace.execution_graph?.nodes ?? [];
  const supersededTasks = new Set(nodes.filter((node) => node.superseded).map((node) => node.task_id));
  const byTasks = group.task_ids.length > 0 && group.task_ids.every((id) => supersededTasks.has(id));
  if (!byTasks && group.status !== "superseded") return null;
  const current = history.find((entry) => entry.is_current) ?? history[history.length - 1];
  let tokensIn = 0;
  let tokensOut = 0;
  for (const child of group.children) {
    tokensIn += num(child.tokens_in) ?? num(child.tokens_input) ?? 0;
    tokensOut += num(child.tokens_out) ?? num(child.tokens_output) ?? 0;
  }
  const costUsd = num(group.metrics?.cost_usd) ?? num(group.metrics?.usd);
  return {
    replanVersion: current?.version ?? null,
    sunkMs: groupDurationMs(group),
    tokensIn: tokensIn > 0 ? tokensIn : null,
    tokensOut: tokensOut > 0 ? tokensOut : null,
    costUsd,
  };
}

function childRows(trace: DeliveryTrace, group: DeliveryRunGroup): AttemptChildRow[] {
  return group.children.map((child, index) => {
    const dispatchId = childDispatchId(child);
    const taskId = str(child.task_id);
    let tryNo: number | null = null;
    let gates: Array<{ type: string; passed: boolean }> = [];
    const taskIds = taskId ? [taskId] : group.task_ids;
    for (const candidate of taskIds) {
      const match = lifecycleTries(trace, candidate).find(
        (tryItem) => !!dispatchId && tryItem.dispatch_id === dispatchId,
      );
      if (match) {
        tryNo = match.try;
        gates = match.gate_results.map((gate) => ({ type: gate.type, passed: gate.passed }));
        break;
      }
    }
    const error = (child.error as Record<string, unknown> | undefined)?.message ?? child.failure_reason;
    return {
      key: `${str(child.child_id) || dispatchId || index}`,
      taskLabel: taskId || str(child.child_id) || dispatchId || `lane-${index + 1}`,
      lane: str(child.worker_id) || str(child.role_instance) || str(child.role) || str(child.backend) || "—",
      tryNo,
      status: str(child.status) || "—",
      durationMs: num(child.duration_ms),
      dispatchId,
      gates,
      note: str(error),
    };
  });
}

function attemptFocus(group: DeliveryRunGroup, related: DeliveryRunTraceSpan[]): TraceFocus {
  let start = group.started_at ?? null;
  let end = group.ended_at ?? null;
  if (!start || !end) {
    const starts = related.map((span) => parseTs(span.started_at)).filter((v): v is number => v !== null);
    const ends = related
      .map((span) => parseTs(span.ended_at) ?? parseTs(span.started_at))
      .filter((v): v is number => v !== null);
    if (!start && starts.length) start = new Date(Math.min(...starts)).toISOString();
    if (!end && ends.length) end = new Date(Math.max(...ends)).toISOString();
  }
  return { focusWindow: { start_ts: start, end_ts: end }, focusRunId: group.group_id };
}

export function buildAttemptRows(trace: DeliveryTrace): AttemptRow[] {
  const groups = trace.run_groups ?? [];
  const spans = trace.trace?.spans ?? [];
  // Attempt numbering: per stage, ordered by started_at (nulls keep payload order).
  const byStage = new Map<string, DeliveryRunGroup[]>();
  for (const group of groups) {
    const stage = group.stage_id || group.label || group.group_id;
    const bucket = byStage.get(stage) ?? [];
    bucket.push(group);
    byStage.set(stage, bucket);
  }
  const attemptNo = new Map<string, number>();
  for (const bucket of byStage.values()) {
    const sorted = [...bucket].sort((a, b) => (parseTs(a.started_at) ?? Number.MAX_SAFE_INTEGER) - (parseTs(b.started_at) ?? Number.MAX_SAFE_INTEGER));
    sorted.forEach((group, index) => attemptNo.set(group.group_id, index + 1));
  }
  const rows = groups.map((group): AttemptRow => {
    const stage = group.stage_id || group.label || group.group_id;
    const attempt = attemptNo.get(group.group_id) ?? 1;
    const superseded = supersededInfo(trace, group);
    const dispatchIds = runDispatchIds(trace, group);
    const related = runSpans(spans, group, dispatchIds);
    const children = childRows(trace, group);
    const doneChildren = children.filter((child) => DONE_STATUSES.includes(child.status.toLowerCase())).length;
    const aggregateWait = num(group.metrics?.aggregate_wait_ms);
    const isFanout = /fanout|aggregate|barrier/i.test(`${group.kind} ${group.operator_kind ?? ""}`);
    return {
      group,
      stage,
      attempt,
      name: `run:${stage}:#${attempt}`,
      statusKind: attemptStatusKind(group.status, !!superseded),
      startedAt: group.started_at ?? null,
      endedAt: group.ended_at ?? null,
      durationMs: groupDurationMs(group),
      childrenDone: doneChildren,
      childrenTotal: children.length,
      superseded,
      seqRange: seqRange(related),
      primaryDispatchId: dispatchIds[0] ?? null,
      children,
      aggregate: aggregateWait !== null || isFanout
        ? { waitMs: aggregateWait, tasks: group.task_ids.length, events: group.source_event_ids?.length ?? 0 }
        : null,
      artifacts: [
        ...(group.artifact_refs ?? []),
        ...related.flatMap((span) => span.evidence_refs ?? []),
      ].slice(0, 12),
      focus: attemptFocus(group, related),
    };
  });
  return rows.sort((a, b) => (parseTs(a.startedAt) ?? Number.MAX_SAFE_INTEGER) - (parseTs(b.startedAt) ?? Number.MAX_SAFE_INTEGER));
}

export function summarizeAttempts(rows: AttemptRow[]): RunsSummary {
  let success = 0;
  let rerun = 0;
  let sunkMs = 0;
  for (const row of rows) {
    if (row.statusKind === "completed") success += 1;
    if (row.attempt > 1) rerun += 1;
    sunkMs += row.superseded?.sunkMs ?? 0;
  }
  return { success, total: rows.length, rerun, sunkMs };
}
