import { CalendarClock } from "lucide-react";
import { useUiPreferences } from "../../app/UiPreferences";

export function AutomationDirectory({ items, selectedId, onSelect }: { items: Record<string, unknown>[]; selectedId: string; onSelect: (id: string) => void }) {
  const { t } = useUiPreferences();
  if (!items.length) return null;
  return <nav className="automation-directory" aria-label={t("Automations")}>
    {items.map((item) => {
      const id = String(item.automation_id || "");
      return <button type="button" key={id} aria-pressed={selectedId === id} onClick={() => onSelect(id)}>
        <span className="automation-directory-name"><CalendarClock size={17} aria-hidden="true" /><span><strong>{String(item.title || id)}</strong><small>{String(item.window || item.automation_id)}</small></span></span>
        <span className="automation-directory-next">{t("Next run")} · {String(item.next_run || t("Manual"))}</span>
        <span className="overview-state">{String(item.status || "idle")}</span>
      </button>;
    })}
  </nav>;
}
