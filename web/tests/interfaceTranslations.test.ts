import { interfaceTranslations } from '../src/settings/interfaceTranslations.js';
import { parsePreferences } from '../src/settings/preferences.js';
import { setSettingsPreference } from '../src/settings/useSettingsPreferences.js';
import { uiText, uiPair } from '../src/settings/uiText.js';

const storage = new Map<string, string>();
function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(message);
}
function equal(actual: unknown, expected: unknown, message: string) {
  assert(Object.is(actual, expected), `${message}: ${String(actual)} !== ${String(expected)}`);
}
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
  getItem: (key: string) => storage.get(key) ?? null,
  setItem: (key: string, value: string) => storage.set(key, value),
} });
const keys = new Set<string>();
for (const row of interfaceTranslations) {
  assert(!keys.has(row[0]), `unique translation key: ${row[0]}`);
  keys.add(row[0]);
  equal(row.length, 4, `four locales: ${row[0]}`);
  assert(row.every(value => value.trim()), `nonempty translations: ${row[0]}`);
}
for (const [language, tasks] of [['en', 'Tasks'], ['zh', '任务'], ['ja', 'タスク'], ['ko', '작업']] as const) {
  equal(parsePreferences({ language }).language, language, 'preference language');
  setSettingsPreference('language', language);
  equal(uiText('Tasks'), tasks, 'plain UI translation');
  equal(uiText(' Tasks '), ` ${tasks} `, 'whitespace-preserving translation');
  equal(uiPair('Tasks', '任务'), tasks, 'paired UI translation');
  equal(uiText('user-authored evidence: Tasks'), 'user-authored evidence: Tasks', 'user content unchanged');
  equal(JSON.parse(storage.get('zf.issuePreferences')!).language, language, 'preference persistence');
}
console.log('Four-locale interface catalogue and preference persistence passed.');
