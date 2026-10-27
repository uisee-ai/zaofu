import { createContext, useContext, useLayoutEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { DEFAULT_PREFERENCES, UI_PREFERENCES_KEY, normalizePreferences, readPreferences, resolvedLanguage, setCurrentPreferences } from "../lib/uiPreferences";
import type { UiPreferences } from "../lib/uiPreferences";
import { translate } from "../lib/uiMessages";
interface PreferencesContext {
  preferences: UiPreferences;
  updatePreferences: (patch: Partial<UiPreferences>) => void;
  t: (message: string) => string;
  storageError: boolean;
}
const Context = createContext<PreferencesContext>({ preferences: DEFAULT_PREFERENCES, updatePreferences: () => {}, t: (s) => s, storageError: false });
export function UiPreferencesProvider({ children }: { children: ReactNode }) {
  const [preferences, setPreferences] = useState(() => {
    const initial = readPreferences();
    setCurrentPreferences(initial);
    return initial;
  });
  const [systemLanguage, setSystemLanguage] = useState(navigator.language);
  const [storageError, setStorageError] = useState(false);
  const language = resolvedLanguage(preferences, systemLanguage);
  useLayoutEffect(() => {
    setCurrentPreferences(preferences);
    const root = document.documentElement;
    const scheme = window.matchMedia("(prefers-color-scheme: dark)");
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => {
      root.dataset.theme = preferences.theme === "system" ? (scheme.matches ? "dark" : "light") : preferences.theme;
      root.dataset.accent = preferences.accent;
      root.dataset.sidebarDensity = preferences.sidebarDensity;
      root.dataset.taskDensity = preferences.taskDensity;
      root.dataset.motion = preferences.motion === "reduce" || motion.matches ? "reduce" : "full";
      root.lang = language;
    };
    apply();
    scheme.addEventListener("change", apply);
    motion.addEventListener("change", apply);
    return () => { scheme.removeEventListener("change", apply); motion.removeEventListener("change", apply); };
  }, [preferences, language]);
  useLayoutEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === UI_PREFERENCES_KEY || event.key === null) { const next = readPreferences(); setCurrentPreferences(next); setPreferences(next); }
    };
    const onLanguage = () => setSystemLanguage(navigator.language);
    window.addEventListener("storage", onStorage);
    window.addEventListener("languagechange", onLanguage);
    return () => { window.removeEventListener("storage", onStorage); window.removeEventListener("languagechange", onLanguage); };
  }, []);
  const value = useMemo<PreferencesContext>(() => ({
    preferences, storageError, t: (message) => translate(message, language),
    updatePreferences: (patch) => {
      const next = normalizePreferences({ ...preferences, ...patch });
      setCurrentPreferences(next);
      setPreferences(next);
      try { window.localStorage.setItem(UI_PREFERENCES_KEY, JSON.stringify(next)); setStorageError(false); }
      catch { setStorageError(true); }
    },
  }), [preferences, language, storageError]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
export function useUiPreferences() { return useContext(Context); }
