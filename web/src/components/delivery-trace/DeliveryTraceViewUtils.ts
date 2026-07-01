import type {
  DeliveryTaskTry,
  DeliveryTrace,
  DeliveryTraceCycle,
  DeliveryTracePhase,
} from "../../api/types";

export function copyText(value: string): void {
  void navigator.clipboard?.writeText(value);
}

// dispatch-{短码}: full id stays in title/copy; display keeps the id tail
// (timestamp prefixes collide, tails don't).
export function shortDispatch(dispatchId: string): string {
  const core = dispatchId.replace(/^dispatch-/, "");
  return core.length > 8 ? core.slice(-8) : core;
}

export function latestDispatchTry(tries: DeliveryTaskTry[] | undefined): DeliveryTaskTry | null {
  for (let i = (tries?.length ?? 0) - 1; i >= 0; i -= 1) {
    if (tries![i].dispatch_id) return tries![i];
  }
  return null;
}

// Task-level seq span = min(seq_first)..max(seq_last) across tries.
export function taskSeqRange(tries: DeliveryTaskTry[] | undefined): { first: number; last: number } | null {
  let first: number | null = null;
  let last: number | null = null;
  for (const item of tries ?? []) {
    if (item.seq_first != null && (first === null || item.seq_first < first)) first = item.seq_first;
    if (item.seq_last != null && (last === null || item.seq_last > last)) last = item.seq_last;
  }
  if (first === null && last === null) return null;
  return { first: first ?? (last as number), last: last ?? (first as number) };
}

export function seqRangeLabel(first?: number | null, last?: number | null): string | null {
  if (first == null && last == null) return null;
  return `seq[${first ?? "—"}..${last ?? "—"}]`;
}

// Trace span ids follow `event:{seq}` — the seq doubles as the Observability
// anchor. Non-conforming ids return null (graceful degradation).
export function parseSpanSeq(spanId: string): number | null {
  const match = /^event:(\d+)$/.exec(spanId);
  return match ? Number(match[1]) : null;
}

// Runs → Trace handoff payload (state lives in DeliveryTraceTabs).
export interface TraceFocus {
  focusWindow: { start_ts?: string | null; end_ts?: string | null };
  focusRunId: string;
}

export function clockLabel(ts?: string | null): string {
  if (!ts) return "—";
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return "—";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function parseTs(ts?: string | null): number | null {
  if (!ts) return null;
  const ms = Date.parse(ts);
  return Number.isNaN(ms) ? null : ms;
}

export function dtTone(status: string): "ok" | "warn" | "err" | "info" | "muted" {
  if (["done", "passed", "ok", "ready", "shipped", "satisfied"].includes(status)) return "ok";
  if (["blocked", "failed", "error", "rejected"].includes(status)) return "err";
  if (["in_progress", "running"].includes(status)) return "info";
  if (["warning", "waiting", "pending", "needs_recovery", "rework"].includes(status)) return "warn";
  return "muted";
}

export function traceCycles(trace: DeliveryTrace): DeliveryTraceCycle[] {
  if ((trace.cycles?.length ?? 0) > 0) return trace.cycles!;
  return (trace.phases ?? []).map(phaseAsCycle);
}

export function currentCycle(trace: DeliveryTrace): DeliveryTraceCycle | null {
  const cycles = traceCycles(trace);
  if (cycles.length === 0) return null;
  return cycles.find((cycle) => !["done", "passed", "completed", "shipped", "adopted", "integrated"].includes(cycle.status)) ?? cycles[cycles.length - 1] ?? null;
}

export function cycleMetaLabel(cycle: DeliveryTraceCycle): string {
  if (cycle.completion_rate != null || cycle.pass_rate != null) {
    return `${percentLabel(cycle.completion_rate)} complete / ${percentLabel(cycle.pass_rate)} pass`;
  }
  return `${cycle.events?.length ?? 0} events / ${cycle.task_ids?.length ?? cycle.task_count ?? 0} tasks`;
}

export function cycleName(cycle: DeliveryTraceCycle): string {
  return String(cycle.phase || cycle.cycle_id || cycle.kind || "cycle");
}

export function formatDuration(durationMs: number | null | undefined): string {
  if (durationMs == null) return "-";
  if (durationMs < 1000) return `${durationMs}ms`;
  const seconds = Math.round(durationMs / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
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

function percentLabel(value: number | null | undefined): string {
  return value == null ? "-" : `${Math.round(value * 100)}%`;
}
