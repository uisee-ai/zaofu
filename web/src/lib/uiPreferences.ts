export const UI_PREFERENCES_KEY = "zf.uiPreferences.v1";
export const ACCENTS = ["indigo", "blue", "violet", "teal", "rose", "graphite"] as const;
export interface UiPreferences {
  theme: "light" | "dark" | "system";
  accent: typeof ACCENTS[number];
  sidebarDensity: "comfortable" | "compact";
  taskDensity: "comfortable" | "compact";
  motion: "system" | "reduce";
  language: "system" | "en" | "zh-CN";
  timeZone: string;
  dateStyle: "medium" | "short" | "iso";
  hourCycle: "h12" | "h23";
}
export const DEFAULT_PREFERENCES: UiPreferences = {
  theme: "system", accent: "indigo", sidebarDensity: "comfortable", taskDensity: "comfortable",
  motion: "system", language: "system", timeZone: "system", dateStyle: "medium", hourCycle: "h23",
};
type StorageReader = Pick<Storage, "getItem">;
function choice<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return allowed.includes(value as T) ? value as T : fallback;
}
export function validTimeZone(value: unknown): value is string {
  if (typeof value !== "string" || !value.trim()) return false;
  if (value === "system") return true;
  try { new Intl.DateTimeFormat("en", { timeZone: value }).format(); return true; } catch { return false; }
}
export function normalizePreferences(value: unknown): UiPreferences {
  const p = value && typeof value === "object" ? value as Partial<UiPreferences> : {};
  return {
    theme: choice(p.theme, ["light", "dark", "system"], "system"),
    accent: choice(p.accent, ACCENTS, "indigo"),
    sidebarDensity: choice(p.sidebarDensity, ["comfortable", "compact"], "comfortable"),
    taskDensity: choice(p.taskDensity, ["comfortable", "compact"], "comfortable"),
    motion: choice(p.motion, ["system", "reduce"], "system"),
    language: choice(p.language, ["system", "en", "zh-CN"], "system"),
    timeZone: validTimeZone(p.timeZone) ? p.timeZone : "system",
    dateStyle: choice(p.dateStyle, ["medium", "short", "iso"], "medium"),
    hourCycle: choice(p.hourCycle, ["h12", "h23"], "h23"),
  };
}
export function readPreferences(storage?: StorageReader): UiPreferences {
  try {
    const source = storage ?? (typeof window === "undefined" ? undefined : window.localStorage);
    const raw = source?.getItem(UI_PREFERENCES_KEY);
    if (raw) {
      try { return normalizePreferences(JSON.parse(raw)); } catch { /* recover legacy theme below */ }
    }
    return normalizePreferences({ theme: source?.getItem("zf.themeMode") });
  } catch { return { ...DEFAULT_PREFERENCES }; }
}
let currentPreferences: UiPreferences = DEFAULT_PREFERENCES;
export function getUiPreferences(): UiPreferences { return currentPreferences; }
export function setCurrentPreferences(p: UiPreferences): void { currentPreferences = p; }
export function resolvedLanguage(p: UiPreferences, systemLanguage?: string): "en" | "zh-CN" {
  const language = p.language === "system"
    ? (systemLanguage ?? (typeof navigator === "undefined" ? "en" : navigator.language)) : p.language;
  return language.toLowerCase().startsWith("zh") ? "zh-CN" : "en";
}
export function formatDateTime(value: string | null | undefined, p = currentPreferences, timeOnly = false): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const locale = resolvedLanguage(p);
  const timeZone = p.timeZone === "system" ? undefined : p.timeZone;
  const timeOptions: Intl.DateTimeFormatOptions = { timeZone, hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: p.hourCycle };
  if (timeOnly) return new Intl.DateTimeFormat(locale, timeOptions).format(date);
  if (p.dateStyle === "iso") {
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(date);
    const part = (type: string) => parts.find((entry) => entry.type === type)?.value;
    return `${part("year")}-${part("month")}-${part("day")} ${new Intl.DateTimeFormat(locale, timeOptions).format(date)}`;
  }
  return new Intl.DateTimeFormat(locale, { timeZone, dateStyle: p.dateStyle, timeStyle: "medium", hourCycle: p.hourCycle }).format(date);
}
