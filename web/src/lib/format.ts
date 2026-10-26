import { formatDateTime } from "./uiPreferences.js";
// 通用格式化 helper —— 从 App.tsx 抽出(WEB-KANBAN-EXTRACT 地基,docs/design/67 §4)。
// 时间展示使用当前设备偏好；App.tsx 与 kanban 组件共享。

export function formatTime(value?: string | null): string {
  return formatDateTime(value, undefined, true);
}

export function formatTokens(value: number | undefined): string {
  return (value ?? 0).toLocaleString("en-US");
}

export function contextBadgeTone(ratio: number | null | undefined): "ok" | "warn" | "err" | "muted" {
  if (ratio == null) return "muted";
  if (ratio >= 0.9) return "err";
  if (ratio >= 0.75) return "warn";
  return "ok";
}

export function contextLabel(ratio: number | null | undefined): string {
  return ratio == null ? "ctx unknown" : `ctx ${Math.round(ratio * 100)}%`;
}
