// 通用格式化 helper —— 从 App.tsx 抽出(WEB-KANBAN-EXTRACT 地基,docs/design/67 §4)。
// App.tsx 与 kanban 组件共享，并遵循用户选择的显示时区。
import { getSettingsPreferences } from '../settings/useSettingsPreferences.js';

export function formatTime(value?: string | null, seconds = true): string {
  if (!value) return "";
  if (!Number.isFinite(Date.parse(value))) return value;
  const prefs = getSettingsPreferences();
  return new Intl.DateTimeFormat(({en:'en-GB',zh:'zh-CN',ko:'ko-KR',ja:'ja-JP'} as const)[prefs.language] || 'en-GB', {
    hour: '2-digit', minute: '2-digit', ...(seconds ? { second: '2-digit' as const } : {}), hour12: false,
    ...(prefs.timeZone === 'local' ? {} : { timeZone: prefs.timeZone }),
  }).format(new Date(value));
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
