import { DEFAULT_PREFERENCES, UI_PREFERENCES_KEY, formatDateTime, normalizePreferences, readPreferences, resolvedLanguage } from "../src/lib/uiPreferences.js";
function assert(condition: unknown, message: string): void { if (!condition) throw new Error(message); }
assert(readPreferences({ getItem: (key) => key === "zf.themeMode" ? "dark" : null }).theme === "dark", "migrate legacy dark theme");
assert(readPreferences({ getItem: (key) => key === UI_PREFERENCES_KEY ? '{"theme":"light","taskDensity":"compact"}' : "dark" }).theme === "light", "new preferences override legacy theme");
assert(readPreferences({ getItem: () => { throw new Error("denied"); } }).theme === "system", "storage denial recovers");
assert(readPreferences({ getItem: (key) => key === UI_PREFERENCES_KEY ? "{broken" : "dark" }).theme === "dark", "corrupt preference document recovers legacy theme");
const sanitized = normalizePreferences({ accent: "invalid", timeZone: "Mars", motion: "invalid", theme: "dark" });
assert(sanitized.theme === "dark" && sanitized.accent === "indigo" && sanitized.timeZone === "system", "invalid individual fields recover without erasing valid values");
assert(resolvedLanguage(DEFAULT_PREFERENCES, "zh-TW") === "zh-CN", "system Chinese locale resolves");
assert(resolvedLanguage(DEFAULT_PREFERENCES, "fr") === "en", "untranslated system locale uses English");
const base = { ...DEFAULT_PREFERENCES, language: "en" as const, dateStyle: "iso" as const, timeZone: "UTC" };
assert(formatDateTime("2026-01-01T01:02:03Z", base) === "2026-01-01 01:02:03", "UTC date and 24 hour time");
assert(formatDateTime("2026-01-01T01:02:03Z", { ...base, timeZone: "America/New_York" }) === "2025-12-31 20:02:03", "timezone crosses calendar date");
assert(formatDateTime("2026-07-01T01:02:03Z", { ...base, timeZone: "America/New_York" }).endsWith("21:02:03"), "daylight saving offset");
assert(formatDateTime("2026-01-01T13:02:03Z", { ...base, hourCycle: "h12" }, true).includes("PM"), "12 hour display applies");
assert(formatDateTime("not-a-date", base) === "not-a-date" && formatDateTime(null, base) === "", "invalid and missing dates remain safe");
console.log("uiPreferences.test.ts OK");
