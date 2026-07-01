// TraceWaterfallModel (T-刀④) — pure projection for the Trace tab tree×瀑布:
// Trace=时间真相(统一时间轴)。Rows: feature → phase → task → try
// (task_lifecycle.tries) → event leaf (spans 按 dispatch_id 优先、时间窗兜底
// 挂入 try)。Axis domain = min/max over spans started_at/ended_at (fallback:
// execution-graph node times); LIFELINE timed points become axis ticks.
// Leaves render-capped per try (`WF_LEAF_CAP`, overflow → "+n more").
import type {
  DeliveryRunTraceSpan,
  DeliveryTaskTry,
  DeliveryTrace,
  DeliveryTraceNode,
} from "../../api/types";
import { clockLabel, parseSpanSeq, parseTs } from "./DeliveryTraceViewUtils";

export const WF_LEAF_CAP = 200;

export interface WfDomain { min: number; max: number; }

export interface WfTick {
  pct: number;
  clock: string;
  kind: string;
  bad: boolean;
  title: string;
}

export interface WfLeaf {
  span: DeliveryRunTraceSpan;
  seq: number | null;
  startMs: number | null;
  endMs: number | null;
}

export interface WfTry {
  key: string;
  tryNo: number;
  outcome: string;
  reworkKind: string | null;
  dispatchId: string | null;
  firstResponseSeconds: number | null;
  startMs: number | null;
  endMs: number | null;
  leaves: WfLeaf[];
  synthetic: boolean; // no lifecycle tries — container for orphan leaves
}

export interface WfTask {
  taskId: string;
  status: string;
  startMs: number | null;
  endMs: number | null;
  seg: { wait: number | null; active: number | null; rework: number | null };
  tries: WfTry[];
}

export interface WfPhase {
  id: string;
  label: string;
  status: string;
  doneCount: number;
  taskCount: number;
  tasks: WfTask[];
}

export interface WfModel {
  domain: WfDomain | null;
  ticks: WfTick[];
  phases: WfPhase[];
  unassigned: WfLeaf[];
  leafTotal: number;
}

export function wfPct(domain: WfDomain, ms: number): number {
  if (domain.max <= domain.min) return 0;
  return Math.min(100, Math.max(0, ((ms - domain.min) / (domain.max - domain.min)) * 100));
}

const TICK_BAD = /fail|reject|block|cancel|timed_out|error|rework/;

function tickKind(eventType: string): string | null {
  if (/replan/.test(eventType)) return "replan";
  if (/task_map/.test(eventType)) return "task_map";
  if (/\bplan|product_delivery/.test(eventType)) return "plan";
  if (/candidate/.test(eventType)) return "candidate";
  if (/gate|review|verify|judge|discriminator/.test(eventType)) return "gate";
  if (/ship|release|merge/.test(eventType)) return "ship";
  return null;
}

// LIFELINE 升级为轴: 只保留有时间戳的点(刻度必须可定位),映射到 domain pct。
function buildTicks(trace: DeliveryTrace, domain: WfDomain | null): WfTick[] {
  if (!domain) return [];
  const timed: Array<{ ms: number; kind: string; bad: boolean; title: string }> = [];
  for (const cycle of trace.cycles ?? []) {
    for (const event of cycle.events ?? []) {
      const kind = event.ts ? tickKind(event.event_type) : null;
      const ms = parseTs(event.ts);
      if (!kind || ms === null) continue;
      timed.push({
        ms,
        kind,
        bad: TICK_BAD.test(event.event_type) || TICK_BAD.test(event.status ?? ""),
        title: `${event.event_type} · ${event.task_id || cycle.cycle_id} · ${event.ts}`,
      });
    }
  }
  if (timed.length < 2) {
    for (const node of trace.execution_graph?.nodes ?? []) {
      for (const [kind, ts] of [["start", node.actual.started_at], ["done", node.actual.completed_at]] as const) {
        const ms = parseTs(ts);
        if (ms === null) continue;
        timed.push({
          ms,
          kind,
          bad: kind === "done" && TICK_BAD.test(node.actual.status),
          title: `${node.task_id} ${kind} · ${ts}`,
        });
      }
    }
  }
  timed.sort((a, b) => a.ms - b.ms);
  return timed.slice(-40).map((point) => ({
    pct: wfPct(domain, point.ms),
    clock: clockLabel(new Date(point.ms).toISOString()),
    kind: point.kind,
    bad: point.bad,
    title: point.title,
  }));
}

function spanLeaf(span: DeliveryRunTraceSpan): WfLeaf {
  const startMs = parseTs(span.started_at);
  return {
    span,
    seq: parseSpanSeq(span.span_id),
    startMs,
    endMs: parseTs(span.ended_at) ?? startMs,
  };
}

// Try windows: dispatched_at → next try's dispatched_at (last try open-ended).
function tryWindows(tries: DeliveryTaskTry[], taskEndMs: number | null): Array<{ start: number | null; end: number | null }> {
  return tries.map((tryItem, index) => {
    const start = parseTs(tryItem.dispatched_at);
    const next = parseTs(tries[index + 1]?.dispatched_at);
    return { start, end: next ?? taskEndMs };
  });
}

function buildTask(
  taskId: string,
  trace: DeliveryTrace,
  node: DeliveryTraceNode | undefined,
  taskSpans: WfLeaf[],
): WfTask {
  const metrics = trace.flow_metrics?.tasks?.[taskId];
  const tries = trace.task_lifecycle?.tasks?.[taskId]?.tries ?? [];
  const spanStarts = taskSpans.map((leaf) => leaf.startMs).filter((v): v is number => v !== null);
  const spanEnds = taskSpans.map((leaf) => leaf.endMs).filter((v): v is number => v !== null);
  const startMs = parseTs(node?.actual.started_at) ?? (spanStarts.length ? Math.min(...spanStarts) : null);
  const endMs = parseTs(node?.actual.completed_at) ?? (spanEnds.length ? Math.max(...spanEnds) : null);
  const windows = tryWindows(tries, endMs);
  const wfTries: WfTry[] = tries.map((tryItem, index) => ({
    key: `${taskId}:try${tryItem.try}`,
    tryNo: tryItem.try,
    outcome: tryItem.outcome,
    reworkKind: tryItem.rework_kind ?? null,
    dispatchId: tryItem.dispatch_id ?? null,
    firstResponseSeconds: tryItem.first_response_seconds ?? null,
    startMs: windows[index].start,
    endMs: windows[index].end,
    leaves: [],
    synthetic: false,
  }));
  // Leaf 挂载: run_id == dispatch_id 优先; 否则落进 try 时间窗; 仍未中 → 末 try。
  const orphans: WfLeaf[] = [];
  for (const leaf of taskSpans) {
    let owner = wfTries.find((tryItem) => !!tryItem.dispatchId && leaf.span.run_id === tryItem.dispatchId);
    if (!owner && leaf.startMs !== null) {
      owner = wfTries.find((tryItem) =>
        tryItem.startMs !== null
        && leaf.startMs! >= tryItem.startMs
        && (tryItem.endMs === null || leaf.startMs! <= tryItem.endMs));
    }
    owner = owner ?? wfTries[wfTries.length - 1];
    if (owner) owner.leaves.push(leaf);
    else orphans.push(leaf);
  }
  if (orphans.length) {
    wfTries.push({
      key: `${taskId}:events`,
      tryNo: 0,
      outcome: "events",
      reworkKind: null,
      dispatchId: null,
      firstResponseSeconds: null,
      startMs,
      endMs,
      leaves: orphans,
      synthetic: true,
    });
  }
  for (const tryItem of wfTries) {
    tryItem.leaves.sort((a, b) => (a.startMs ?? 0) - (b.startMs ?? 0));
  }
  return {
    taskId,
    status: node?.actual.status ?? "?",
    startMs,
    endMs,
    seg: {
      wait: metrics?.wait_seconds ?? null,
      active: metrics?.active_seconds ?? null,
      rework: metrics?.rework_seconds ?? null,
    },
    tries: wfTries,
  };
}

export function buildWaterfallModel(trace: DeliveryTrace): WfModel {
  const spans = trace.trace?.spans ?? [];
  const nodes = trace.execution_graph?.nodes ?? [];
  const nodeByTask = new Map(nodes.map((node) => [node.task_id, node]));
  const leaves = spans.map(spanLeaf);

  // Domain: spans first, execution-graph times as degraded fallback.
  const stamps: number[] = [];
  for (const leaf of leaves) {
    if (leaf.startMs !== null) stamps.push(leaf.startMs);
    if (leaf.endMs !== null) stamps.push(leaf.endMs);
  }
  if (!stamps.length) {
    for (const node of nodes) {
      for (const ts of [node.actual.started_at, node.actual.completed_at]) {
        const ms = parseTs(ts);
        if (ms !== null) stamps.push(ms);
      }
    }
  }
  const domain: WfDomain | null = stamps.length >= 2
    ? { min: Math.min(...stamps), max: Math.max(...stamps) }
    : null;

  // Phase groups: trace.phases, else one synthetic group over the graph.
  const leavesByTask = new Map<string, WfLeaf[]>();
  const unassigned: WfLeaf[] = [];
  for (const leaf of leaves) {
    const taskId = leaf.span.task_id ?? "";
    if (!taskId) {
      unassigned.push(leaf);
      continue;
    }
    const bucket = leavesByTask.get(taskId) ?? [];
    bucket.push(leaf);
    leavesByTask.set(taskId, bucket);
  }
  const phaseDefs = (trace.phases?.length ?? 0) > 0
    ? trace.phases!.map((phase) => ({
      id: phase.phase_id,
      label: phase.phase_id,
      status: phase.status,
      doneCount: phase.done_count,
      taskCount: phase.task_count,
      taskIds: phase.task_ids ?? [],
    }))
    : nodes.length
      ? [{
        id: "__all_tasks__",
        label: "tasks (no phase projection)",
        status: trace.status,
        doneCount: nodes.filter((node) => ["done", "cancelled"].includes(node.actual.status)).length,
        taskCount: nodes.length,
        taskIds: nodes.map((node) => node.task_id),
      }]
      : [];
  const claimed = new Set<string>();
  const phases: WfPhase[] = phaseDefs.map((def) => ({
    id: def.id,
    label: def.label,
    status: def.status,
    doneCount: def.doneCount,
    taskCount: def.taskCount,
    tasks: def.taskIds.map((taskId) => {
      claimed.add(taskId);
      return buildTask(taskId, trace, nodeByTask.get(taskId), leavesByTask.get(taskId) ?? []);
    }),
  }));
  // Spans pointing at tasks outside every phase keep their evidence visible.
  for (const [taskId, bucket] of leavesByTask) {
    if (!claimed.has(taskId)) unassigned.push(...bucket);
  }
  unassigned.sort((a, b) => (a.startMs ?? 0) - (b.startMs ?? 0));
  return {
    domain,
    ticks: buildTicks(trace, domain),
    phases,
    unassigned,
    leafTotal: leaves.length,
  };
}
