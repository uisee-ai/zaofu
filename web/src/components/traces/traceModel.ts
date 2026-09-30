import type { TraceSummary, TraceTimelineEvent } from "../../api/types";
import { formatSettingsTime } from '../../settings/useSettingsPreferences.js';

export type TraceStatusFilter = "all" | "running" | "completed" | "failed" | "blocked" | "observed";
export type TraceDurationFilter = "all" | "short" | "medium" | "long" | "unknown";

export interface TraceFilters {
  query: string;
  status: TraceStatusFilter;
  duration: TraceDurationFilter;
  role: string;
  backend: string;
}


export function traceMatchesFilters(row: TraceSummary, filters: TraceFilters): boolean {
  if (filters.status !== "all" && traceStatus(row) !== filters.status) return false;
  if (filters.duration !== "all" && traceDurationBucket(row) !== filters.duration) return false;
  const role = filters.role.trim().toLowerCase();
  if (role && !(row.actors ?? []).some((value) => value.toLowerCase().includes(role))) return false;
  const backend = filters.backend.trim().toLowerCase();
  if (backend && !(row.backends ?? []).some((value) => value.toLowerCase().includes(backend))) return false;
  const query = filters.query.trim().toLowerCase();
  if (!query) return true;
  return [
    row.trace_id,
    row.last_type,
    ...(row.task_ids ?? []),
    ...(row.actors ?? []),
    ...(row.backends ?? []),
  ].join(" ").toLowerCase().includes(query);
}

export function traceStatus(
  row: Pick<TraceSummary, "status" | "last_type">,
): Exclude<TraceStatusFilter, "all"> {
  const value = `${row.status || ""} ${row.last_type || ""}`.toLowerCase();
  if (value.includes("blocked")) return "blocked";
  if (/(failed|error|rejected)/.test(value)) return "failed";
  if (/(completed|done|passed|approved|accepted)/.test(value)) return "completed";
  if (/(running|started|progress|in_progress|dispatched)/.test(value)) return "running";
  return "observed";
}

export function traceDurationBucket(row: TraceSummary): TraceDurationFilter {
  const seconds = traceDurationSeconds(row);
  if (seconds === null) return "unknown";
  if (seconds < 60) return "short";
  if (seconds < 600) return "medium";
  return "long";
}

export function traceDurationSeconds(
  row: Pick<TraceSummary, "duration_seconds" | "first_ts" | "last_ts">,
): number | null {
  if (typeof row.duration_seconds === "number" && Number.isFinite(row.duration_seconds)) {
    return Math.max(0, row.duration_seconds);
  }
  const first = Date.parse(row.first_ts || "");
  const last = Date.parse(row.last_ts || "");
  if (!Number.isFinite(first) || !Number.isFinite(last)) return null;
  return Math.max(0, (last - first) / 1000);
}

export function formatDuration(seconds: number | null | undefined): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "Unknown";
  if (seconds < 1) return "<1s";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`;
}

export function formatTimestamp(value: string): string {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? formatSettingsTime(new Date(timestamp).toISOString(), { year: undefined, day: '2-digit', month: 'short', second: undefined }) : "Unknown";
}

export function readInitialTraceFilters(): TraceFilters {
  if (typeof window === "undefined") return defaultTraceFilters();
  const params = new URLSearchParams(window.location.search);
  const status = params.get("obs_status");
  const duration = params.get("obs_duration");
  return {
    backend: params.get("obs_backend") ?? "",
    duration: isTraceDurationFilter(duration) ? duration : "all",
    query: params.get("obs_q") ?? "",
    role: params.get("obs_role") ?? "",
    status: isTraceStatusFilter(status) ? status : "all",
  };
}

export function readInitialTraceId(): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get("trace_id") ?? "";
}

export function readInitialSpanId(): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get("span_id") ?? "";
}

export function persistTraceFilters(filters: TraceFilters) {
  if (typeof window === "undefined") return;
  const params = new URLSearchParams(window.location.search);
  setOptionalParam(params, "obs_q", filters.query);
  setOptionalParam(params, "obs_role", filters.role);
  setOptionalParam(params, "obs_backend", filters.backend);
  setOptionalParam(params, "obs_status", filters.status === "all" ? "" : filters.status);
  setOptionalParam(params, "obs_duration", filters.duration === "all" ? "" : filters.duration);
  window.history.replaceState(null, "", `${window.location.pathname}?${params.toString()}${window.location.hash}`);
}

export function writeTraceSelection(traceId: string, spanId = "") {
  const params = new URLSearchParams(window.location.search);
  params.set("page", "traces");
  setOptionalParam(params, "trace_id", traceId);
  setOptionalParam(params, "span_id", traceId ? spanId : "");
  window.history.replaceState(null, "", `${window.location.pathname}?${params.toString()}${window.location.hash}`);
}

export function uniqueValues(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))].sort((left, right) => left.localeCompare(right));
}

export function optionValues(values: string[], selected: string): string[] {
  return selected && !values.includes(selected) ? [selected, ...values] : values;
}

export function mergeTraceRows(current: TraceSummary[], incoming: TraceSummary[]): TraceSummary[] {
  const byId = new Map(current.map((row) => [row.trace_id, row]));
  for (const row of incoming) byId.set(row.trace_id, row);
  return [...byId.values()];
}

export function mergeTraceEvents(
  earlier: TraceTimelineEvent[],
  current: TraceTimelineEvent[],
): TraceTimelineEvent[] {
  const byId = new Map<string, TraceTimelineEvent>();
  for (const [index, event] of [...earlier, ...current].entries()) {
    byId.set(event.id || `seq:${event.seq ?? index}`, event);
  }
  return [...byId.values()].sort((left, right) => (left.seq ?? 0) - (right.seq ?? 0));
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function defaultTraceFilters(): TraceFilters {
  return { backend: "", duration: "all", query: "", role: "", status: "all" };
}

function setOptionalParam(params: URLSearchParams, key: string, value: string) {
  if (value.trim()) params.set(key, value.trim());
  else params.delete(key);
}

function isTraceStatusFilter(value: string | null): value is TraceStatusFilter {
  return Boolean(value && ["all", "running", "completed", "failed", "blocked", "observed"].includes(value));
}

function isTraceDurationFilter(value: string | null): value is TraceDurationFilter {
  return Boolean(value && ["all", "short", "medium", "long", "unknown"].includes(value));
}
