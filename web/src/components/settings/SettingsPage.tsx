import { Building2, Check, Globe2, Palette, Clock3, Sun, Moon, Monitor } from "lucide-react";
import { useState } from "react";
import type { ReactNode, CSSProperties } from "react";
import type { Snapshot } from "../../api/types";
import { useUiPreferences } from "../../app/UiPreferences";
import { ACCENTS, formatDateTime, validTimeZone } from "../../lib/uiPreferences";
import type { UiPreferences } from "../../lib/uiPreferences";

const SECTIONS = [
  { id: "workspace", label: "Workspace", icon: Building2 },
  { id: "appearance", label: "Appearance", icon: Palette },
  { id: "language", label: "Language", icon: Globe2 },
  { id: "datetime", label: "Date & time", icon: Clock3 },
] as const;
const ZONES = ["system", "UTC", "America/New_York", "America/Los_Angeles", "Europe/London", "Europe/Berlin", "Asia/Shanghai", "Asia/Tokyo", "Asia/Kolkata", "Australia/Sydney"];
function allTimeZones(): string[] {
  const intl = Intl as typeof Intl & { supportedValuesOf?: (key: "timeZone") => string[] };
  return [...new Set([...ZONES, ...(intl.supportedValuesOf?.("timeZone") ?? [])])];
}
const TIME_ZONES = allTimeZones();
function SettingRow({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return <div className="general-setting-row"><div><div className="general-setting-label">{title}</div>{description && <p>{description}</p>}</div><div className="general-setting-control">{children}</div></div>;
}
function ThemePreview({ mode }: { mode: "light" | "dark" | "system" }) {
  return <span className={`appearance-preview preview-${mode}`} aria-hidden="true">
    {mode === "system" ? <><ThemePreview mode="light" /><ThemePreview mode="dark" /></> : <>
      <span className="appearance-preview-rail"><i /><i /><i /><i /></span>
      <span className="appearance-preview-main"><b /><i /><i /><em /></span>
    </>}
  </span>;
}
export function SettingsPage({ snapshot }: { snapshot: Snapshot | null }) {
  const { preferences: p, updatePreferences: update, t, storageError } = useUiPreferences();
  const [section, setSection] = useState(() => {
    const fromUrl = new URLSearchParams(window.location.search).get("settings");
    return SECTIONS.find((item) => item.id === fromUrl)?.id ?? "workspace";
  });
  const [previewDate] = useState(() => new Date().toISOString());
  const title = SECTIONS.find((item) => item.id === section)!.label;
  const select = <K extends keyof UiPreferences>(key: K, label: string, options: Array<[UiPreferences[K], string]>) => (
    <select aria-label={t(label)} value={p[key]} onChange={(event) => update({ [key]: event.target.value })}>
      {options.map(([value, name]) => <option value={value} key={value}>{t(name)}</option>)}
    </select>
  );
  const density = (key: "sidebarDensity" | "taskDensity", label: string) => <div className="settings-density" role="group" aria-label={t(label)}>
    {(["comfortable", "compact"] as const).map((value) => <button key={value} type="button" aria-pressed={p[key] === value} onClick={() => update({ [key]: value })}>{t(value === "comfortable" ? "Comfortable" : "Compact")}</button>)}
  </div>;
  return <div className="general-settings" data-testid="general-settings">
    <div className="general-settings-layout">
      <nav className="general-settings-nav" aria-label={t("General")}>
        <span className="settings-nav-caption">{t("General")}</span>
        {SECTIONS.map(({ id, label, icon: Icon }) => <button key={id} type="button" aria-current={section === id ? "page" : undefined} onClick={() => {
          setSection(id);
          const url = new URL(window.location.href); url.searchParams.set("settings", id); window.history.replaceState(null, "", url);
        }}><Icon size={16} aria-hidden="true" />{t(label)}</button>)}
      </nav>
      <section className="general-settings-content" aria-label={t(title)}>
        <h3>{t(title)}</h3>
        {section === "workspace" && <>
          <p className="settings-description">{t("Managed by the project configuration.")}</p>
          {[["Project name", snapshot?.project.name], ["Project root", snapshot?.project.root], ["State directory", snapshot?.project.state_dir], ["Configuration", "zf.yaml"]].map(([label, value]) =>
            <SettingRow key={label} title={t(label || "")}><span className="settings-readonly">{value || "—"}</span></SettingRow>)}
          <details className="settings-runtime"><summary>{t("Runtime boundary")}</summary><dl><dt>Control plane</dt><dd>zf.yaml</dd><dt>Runtime truth</dt><dd>TaskStore / EventWriter</dd><dt>Web actions</dt><dd>{snapshot?.runtime.actions?.mutation_enabled ? "Token / session gated" : "Read-only"}</dd></dl></details>
        </>}
        {section === "appearance" && <>
          <p className="settings-description">{t("Choose how ZaoFu looks on this device.")}</p>
          <div className="settings-section-intro"><h4>{t("Theme")}</h4></div>
          <div className="appearance-themes" role="group" aria-label={t("Theme")}>
            {(["light", "dark", "system"] as const).map((mode) => <button type="button" key={mode} aria-pressed={p.theme === mode} aria-label={t(mode === "light" ? "Light" : mode === "dark" ? "Dark" : "System")} onClick={() => update({ theme: mode })}>
              <ThemePreview mode={mode} />
              <span><span className="theme-card-label">{mode === "light" ? <Sun size={14} /> : mode === "dark" ? <Moon size={14} /> : <Monitor size={14} />}{t(mode === "light" ? "Light" : mode === "dark" ? "Dark" : "System")}</span>{p.theme === mode && <Check size={14} aria-hidden="true" />}</span>
            </button>)}
          </div>
          <div className="settings-section-intro"><h4>{t("Accent color")}</h4></div>
          <SettingRow title={t("Accent")} description={t("Used for selections, links and focus.")}><div className="accent-options" role="group" aria-label={t("Accent color")}>
            {ACCENTS.map((accent) => <button type="button" key={accent} aria-label={t(accent)} title={t(accent)} aria-pressed={p.accent === accent} style={{ "--swatch": `var(--accent-${accent})` } as CSSProperties} onClick={() => update({ accent })}>{p.accent === accent && <Check size={14} aria-hidden="true" />}</button>)}
          </div></SettingRow>
          <div className="settings-section-intro"><h4>{t("Density")}</h4></div>
          <SettingRow title={t("Sidebar density")} description={t("Spacing between navigation items.")}>{density("sidebarDensity", "Sidebar density")}</SettingRow>
          <SettingRow title={t("Task density")} description={t("Applies to Board, List and Table.")}>{density("taskDensity", "Task density")}</SettingRow>
          <SettingRow title={t("Motion")} description={t("Respect your system preference or reduce animation.")}>{select("motion", "Motion", [["system", "System"], ["reduce", "Reduce motion"]])}</SettingRow>
        </>}
        {section === "language" && <>
          <SettingRow title={t("Display language")} description={t("Choose the language for navigation and these workspace views.")}>{select("language", "Display language", [["system", "System"], ["en", "English"], ["zh-CN", "简体中文"]])}</SettingRow>
          <p className="settings-footnote">{t("Navigation, Tasks, Inbox and General settings are translated. Runtime diagnostics and content from your project keep their original language.")}</p>
        </>}
        {section === "datetime" && <>
          <SettingRow title={t("Time zone")} description={t("Changes display only; schedules keep their configured time zone.")}>
            <select aria-label={t("Time zone")} value={p.timeZone} onChange={(event) => validTimeZone(event.target.value) && update({ timeZone: event.target.value })}>{[...new Set([...TIME_ZONES, p.timeZone])].map((zone) => <option key={zone} value={zone}>{zone === "system" ? `${t("System")} (${Intl.DateTimeFormat().resolvedOptions().timeZone})` : zone}</option>)}</select>
          </SettingRow>
          <SettingRow title={t("Date format")}>{select("dateStyle", "Date format", [["medium", "Medium"], ["short", "Short"], ["iso", "YYYY-MM-DD"]])}</SettingRow>
          <SettingRow title={t("Time format")}>{select("hourCycle", "Time format", [["h23", "24 hour"], ["h12", "12 hour"]])}</SettingRow>
          <div className="settings-time-preview"><span>{t("Preview")}</span><time dateTime={previewDate}>{formatDateTime(previewDate, p)}</time></div>
        </>}
        <p className={`settings-footnote ${storageError ? "error" : ""}`} role="status">{t(storageError ? "Preferences could not be saved. Changes apply to this tab only." : "Local preferences are saved on this device.")}</p>
      </section>
    </div>
  </div>;
}
