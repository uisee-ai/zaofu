import type { LoopViewAttempt, LoopViewTask } from "../../api/types";

const GAP_MS = 30 * 60 * 1000;
const time = (value?: string) => value ? Date.parse(value) : NaN;

export const timelineTaskKey = (task: LoopViewTask) => JSON.stringify([task.id, task.stage_id ?? "", task.source]);
export const timelineAttemptKey = (attempt: LoopViewAttempt) => JSON.stringify([
  attempt.started_ts, attempt.role,
  attempt.orphan || !attempt.started_ts ? [attempt.terminal?.ts, attempt.terminal?.seq, attempt.terminal?.type] : null,
]);
export function selectedAttemptIndices(task: LoopViewTask, keys: string[]): number[] {
  const matches = new Map<string, number>();
  task.attempts.forEach((attempt, index) => {
    const key = timelineAttemptKey(attempt);
    matches.set(key, matches.has(key) ? -1 : index);
  });
  return keys.flatMap((key) => {
    const index = matches.get(key) ?? -1;
    return index >= 0 ? [index] : [];
  });
}

export function attemptState(attempt: LoopViewAttempt) {
  if (attempt.terminal) return /\.(failed|rejected)$/.test(attempt.terminal.type) ? "fail" : "done";
  return attempt.counted ? "open" : "uncounted";
}

export function attemptRange(attempt: LoopViewAttempt, observed: number) {
  const start = time(attempt.started_ts), terminal = time(attempt.terminal?.ts);
  const point = Number.isFinite(start) ? start : terminal;
  const end = attempt.terminal ? terminal : observed;
  const known = !attempt.orphan && Number.isFinite(start) && Number.isFinite(end)
    && end >= start && Boolean(attempt.terminal || attempt.counted);
  return { start: point, end: known ? end : point, known };
}

export function durationLabel(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds >= 86400) return `${Math.floor(seconds / 86400)}d ${Math.floor(seconds % 86400 / 3600)}h`;
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
  if (seconds >= 60) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${seconds}s`;
}

export function timelineModel(tasks: LoopViewTask[], lastObserved: string, compressed: boolean) {
  let observed = time(lastObserved);
  for (const task of tasks) for (const attempt of task.attempts) {
    for (const value of [time(attempt.started_ts), time(attempt.terminal?.ts)]) {
      if (Number.isFinite(value)) observed = Number.isFinite(observed) ? Math.max(observed, value) : value;
    }
  }
  if (!Number.isFinite(observed)) return null;
  const ranges = tasks.flatMap((task) => task.attempts.map((attempt) => attemptRange(attempt, observed)))
    .filter((range) => Number.isFinite(range.start)).sort((a, b) => a.start - b.start);
  if (!ranges.length) return null;
  const start = ranges[0].start, end = Math.max(observed, start + 1000);
  const windows: Array<{ start: number; end: number }> = [];
  for (const range of [...ranges, { start: end, end }]) {
    const previous = windows.at(-1);
    if (previous && range.start - previous.end <= GAP_MS) previous.end = Math.max(previous.end, range.end);
    else windows.push({ start: range.start, end: range.end });
  }
  const gapCount = compressed ? windows.length - 1 : 0;
  const gapWidth = gapCount ? Math.min(4, 35 / gapCount) : 0;
  const active = windows.reduce((total, window) => total + Math.max(1000, window.end - window.start), 0);
  let offset = 0;
  const spans = (compressed ? windows : [{ start, end }]).map((window) => {
    const width = compressed ? Math.max(1000, window.end - window.start) / active * (100 - gapCount * gapWidth) : 100;
    const span = { ...window, left: offset, width };
    offset += width + gapWidth;
    return span;
  });
  const at = (timestamp: number) => {
    const span = spans.find((item) => timestamp <= item.end) ?? spans[spans.length - 1];
    return Math.min(100, Math.max(0, span.left + Math.max(0, timestamp - span.start) / Math.max(1000, span.end - span.start) * span.width));
  };
  const candidates = compressed ? spans.flatMap((span) => [span.start, span.end])
    : Array.from({ length: 5 }, (_, index) => start + (end - start) * index / 4);
  const ticks: Array<{ time: number; x: number }> = [];
  for (const timestamp of candidates) {
    const x = at(timestamp);
    if (!ticks.length || x - ticks[ticks.length - 1].x >= 18) ticks.push({ time: timestamp, x });
  }
  return { start, end, observed, at, ticks, gaps: spans.slice(0, -1).map((span, index) => ({
    left: span.left + span.width, width: gapWidth, duration: spans[index + 1].start - span.end,
  })) };
}

export interface TimelineSegment {
  left: number;
  right: number;
  indices: number[];
  state: ReturnType<typeof attemptState>;
  point: boolean;
  lane: number;
}

export function timelineSegments(task: LoopViewTask, model: NonNullable<ReturnType<typeof timelineModel>>) {
  const segments: TimelineSegment[] = task.attempts.flatMap<TimelineSegment>((attempt, index) => {
    const range = attemptRange(attempt, model.observed);
    if (!Number.isFinite(range.start)) return [];
    const left = model.at(range.start), right = model.at(range.end);
    return [{ left, right, indices: [index], state: attemptState(attempt), point: !range.known || right - left < 0.8, lane: 0 }];
  }).sort((a, b) => a.left - b.left || a.right - b.right);
  const grouped: TimelineSegment[] = [];
  let cluster: TimelineSegment[] = [];
  const flush = () => {
    if (cluster.length >= 3) {
      const first = cluster[0];
      grouped.push({ ...first, right: Math.max(...cluster.map((item) => item.right)),
        indices: cluster.flatMap((item) => item.indices), point: false,
        state: cluster.some((item) => item.state === "fail") ? "fail"
          : cluster.some((item) => item.state === "open") ? "open"
          : cluster.every((item) => item.state === "uncounted") ? "uncounted" : "done" });
    } else grouped.push(...cluster);
    cluster = [];
  };
  for (const segment of segments) {
    if (cluster.length && (!segment.point || segment.left - cluster[cluster.length - 1].right > 1.5 || segment.right - cluster[0].left > 8)) flush();
    if (segment.point) cluster.push(segment); else grouped.push(segment);
  }
  flush();
  const lanes: number[] = [];
  for (const segment of grouped.sort((a, b) => a.left - b.left)) {
    let lane = lanes.findIndex((right) => right + 1 < segment.left);
    if (lane < 0) lane = lanes.length;
    lanes[lane] = segment.right;
    segment.lane = lane;
  }
  return { segments: grouped, lanes: Math.max(1, lanes.length) };
}
