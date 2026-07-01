// Task 展示 helper —— 从 App.tsx 抽出(WEB-KANBAN-EXTRACT 地基,docs/design/67 §4)。
// 纯的 task→展示值 派生;App.tsx 与 kanban 组件共享。
import type { Task } from "../api/types";
import { formatTime } from "./format";

export interface TaskTelemetry {
  attention: string[];
  contextRatio: number | null;
  inputTokens: number;
  outputTokens: number;
  usd: number;
  workerIds: string[];
}

export function taskPriority(task: Task): number {
  const raw = (task as Task & { priority?: number | string }).priority;
  const value = Number(raw ?? 3);
  return Number.isFinite(value) ? Math.max(0, Math.min(5, value)) : 3;
}

export function taskActorLabel(task: Task): string {
  if (task.assigned_to) return `@${task.assigned_to}`;
  const skill = task.skills_required?.find(Boolean);
  return skill ? skill : "";
}

export function taskRiskBadge(
  task: Task,
  telemetry: TaskTelemetry | undefined,
): { label: string; tone: "ok" | "warn" | "err" | "muted" } {
  if (task.blocked_reason) return { label: "risk blocked", tone: "err" };
  if ((telemetry?.contextRatio ?? 0) >= 0.9) return { label: "risk context", tone: "err" };
  if ((telemetry?.contextRatio ?? 0) >= 0.75) return { label: "risk context", tone: "warn" };
  if (task.retry_count > 0) return { label: "risk rework", tone: "warn" };
  return { label: "risk normal", tone: "muted" };
}

export function latestEventAge(task: Task): string {
  const ts = task.latest_event?.ts;
  if (!ts) return "-";
  const ms = Date.now() - Date.parse(ts);
  if (!Number.isFinite(ms) || ms < 0) return formatTime(ts);
  const minutes = Math.floor(ms / 60000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

export const BACKLOG_REF_KEYS_LOCAL = [
  "spec_ref",
  "plan_ref",
  "tdd_ref",
  "critic_event_id",
  "critic_gate_ref",
  "evidence_contract",
] as const;

export function backlogRefsState(contract: Record<string, unknown> | undefined) {
  const present: string[] = [];
  const missing: string[] = [];
  for (const key of BACKLOG_REF_KEYS_LOCAL) {
    const v = contract?.[key];
    let ok: boolean;
    if (v === null || v === undefined) ok = false;
    else if (typeof v === "string") ok = v.trim() !== "";
    else if (typeof v === "object" && !Array.isArray(v)) ok = Object.keys(v as object).length > 0;
    else ok = Boolean(v);
    if (ok) present.push(key);
    else missing.push(key);
  }
  return { present, missing, total: BACKLOG_REF_KEYS_LOCAL.length };
}

export function routeStatusTone(status: string | undefined): "ok" | "warn" | "err" | "muted" | "info" {
  if (status === "done") return "ok";
  if (status === "running") return "info";
  if (status === "failed" || status === "blocked") return "err";
  return "muted";
}
