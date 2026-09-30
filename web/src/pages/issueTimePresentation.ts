import { formatSettingsTime, getSettingsPreferences } from "../settings/useSettingsPreferences.js";
/** Created/opened dates use the upstream timestamp, never the last mirror update. */
function parseDate(value?: string | null): Date | null {
  if (!value?.trim()) return null;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date : null;
}

export function issueOpenedAgo(createdAt: string | null | undefined, now = Date.now()): string {
  const date = parseDate(createdAt);
  if (!date || !Number.isFinite(now)) return 'Creation date unavailable';
  // Tolerate small upstream/client clock differences without a negative age.
  const seconds = Math.max(0, Math.floor((now - date.getTime()) / 1000));
  if (getSettingsPreferences().language === 'zh') {
    if (seconds < 60) return '刚刚创建';
    const [amount, unit] = seconds < 3600 ? [Math.floor(seconds / 60), '分钟']
      : seconds < 86400 ? [Math.floor(seconds / 3600), '小时']
      : seconds < 2592000 ? [Math.floor(seconds / 86400), '天']
      : seconds < 31536000 ? [Math.floor(seconds / 2592000), '个月']
      : [Math.floor(seconds / 31536000), '年'];
    return `${amount} ${unit}前创建`;
  }
  if (seconds < 60) return 'opened just now';
  if (seconds < 3600) return `opened ${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `opened ${Math.floor(seconds / 3600)}h ago`;
  const days = Math.floor(seconds / 86400);
  if (days < 30) return `opened ${days}d ago`;
  if (days < 365) return `opened ${Math.floor(days / 30)}mo ago`;
  return `opened ${Math.floor(days / 365)}y ago`;
}

export function issueCreatedAt(value?: string | null): { text: string; dateTime?: string; timeZone: string } {
  const date = parseDate(value);
  if (!date) return { text: '—', timeZone: 'Creation date unavailable' };
  const two = (part: number) => String(part).padStart(2, '0');
  const text = `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())} ${two(date.getHours())}:${two(date.getMinutes())}:${two(date.getSeconds())}`;
  const offset = -date.getTimezoneOffset();
  const timeZone = `Local time (UTC${offset >= 0 ? '+' : '-'}${two(Math.floor(Math.abs(offset) / 60))}:${two(Math.abs(offset) % 60)})`;
  return { text: getSettingsPreferences().timeZone === "local" ? text : formatSettingsTime(value || undefined), dateTime: date.toISOString(), timeZone: getSettingsPreferences().timeZone === "local" ? timeZone : getSettingsPreferences().timeZone };
}
