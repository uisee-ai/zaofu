import { useEffect, useState, type ReactNode } from "react";
import { ArrowUpRight, ChevronDown } from "lucide-react";
import type { Feature, RecentEvent, Snapshot } from "../../api/types";
import type { PageId } from "../../app/sharedTypes";
import { getRecentEvents } from "../../api/client";
import { formatTime } from "../../lib/format";
import { useUiPreferences } from "../../app/UiPreferences";

interface OverviewMetric {
  label: string;
  value: string;
  detail: string;
  onClick: () => void;
}

export function OverviewDashboard({ projectId, snapshot, metrics, focus, flow, pulse, insight, counts, events, onOpenPage }: {
  projectId: string;
  snapshot: Snapshot | null;
  metrics: OverviewMetric[];
  focus: ReactNode;
  flow: ReactNode;
  pulse: ReactNode;
  insight: ReactNode;
  counts: Array<{ id: string; title: string; count: number }>;
  events: RecentEvent[];
  onOpenPage: (page: PageId) => void;
}) {
  const { t } = useUiPreferences();
  const [descriptionOpen, setDescriptionOpen] = useState(false);
  const features = snapshot?.features ?? [];
  const total = counts.reduce((sum, item) => sum + item.count, 0);
  const [history, setHistory] = useState<RecentEvent[]>([]);
  const [activityStatus, setActivityStatus] = useState("Loading recent activity…");
  useEffect(() => {
    let current = true;
    setHistory([]);
    setActivityStatus("Loading recent activity…");
    if (!projectId.trim()) return;
    void getRecentEvents(5, projectId).then((items) => {
      if (current) { setHistory(items); setActivityStatus("No recent activity"); }
    }).catch(() => { if (current) setActivityStatus("Recent activity unavailable"); });
    return () => { current = false; };
  }, [projectId]);
  const recent = Array.from(new Map([...history, ...events].map((event) => [event.seq, event])).values())
    .sort((a, b) => Number(b.seq || 0) - Number(a.seq || 0)).slice(0, 5);
  return (
    <section className="project-home overview-dashboard" data-testid="overview-dashboard">
      <header className="workspace-page-heading overview-heading">
        <div>
          <span className="overview-eyebrow">{t("Overview")}</span>
          <h2>{snapshot?.project.name || t("Project overview")}</h2>
          <p className="muted">{snapshot ? `${t("Updated")} ${formatTime(snapshot.generated_at)}` : t("Loading project summary…")}</p>
        </div>
        <button className="icon-button" type="button" onClick={() => onOpenPage("board")}>{t("Open Tasks")} <ArrowUpRight size={14} aria-hidden="true" /></button>
      </header>
      {snapshot?.project.description && (
        <div className={`overview-description ${descriptionOpen ? "is-open" : ""}`}>
          <p id="overview-description" data-testid="project-description">{snapshot.project.description}</p>
          <button type="button" aria-controls="overview-description" aria-expanded={descriptionOpen} onClick={() => setDescriptionOpen(!descriptionOpen)}>
            {t(descriptionOpen ? "Show less" : "Read more")} <ChevronDown size={13} aria-hidden="true" />
          </button>
        </div>
      )}
      <div className="workspace-stat-strip overview-metrics" aria-label={t("Project summary")}>
        {metrics.map((metric) => (
          <button type="button" className="workspace-stat" key={metric.label} onClick={metric.onClick}>
            <span>{t(metric.label)}</span><strong>{snapshot ? metric.value : "—"}</strong>
            <small>{snapshot ? metric.detail : t("Loading…")}</small>
          </button>
        ))}
      </div>
      <div className="overview-columns">
        <section className="overview-card overview-flow"><div className="overview-card-heading"><h3>{t("Execution flow")}</h3><button type="button" onClick={() => onOpenPage("behavior-loop")}>{t("View Loop")} <ArrowUpRight size={13} aria-hidden="true" /></button></div>{flow}</section>
        <div className="overview-main">
          {snapshot ? focus : <section className="overview-card"><h3>{t("Needs attention")}</h3><p className="muted">{t("Loading project summary…")}</p></section>}
          <section className="overview-card overview-deliveries">
            <div className="overview-card-heading"><h3>{t("Delivery progress")}</h3><button type="button" onClick={() => onOpenPage("delivery")}>{t("View all")} <ArrowUpRight size={13} aria-hidden="true" /></button></div>
            {features.length ? features.slice(0, 6).map((feature: Feature) => (
              <button className="overview-delivery-row" type="button" key={feature.id} onClick={() => onOpenPage("delivery")}>
                <span><strong>{feature.title || feature.id}</strong><small>{feature.id}</small></span>
                <span className={`overview-state state-${feature.status}`}>{t(feature.status)}</span>
                <ArrowUpRight size={14} aria-hidden="true" />
              </button>
            )) : <p className="overview-card-empty">{t(snapshot ? "No features yet. Track execution in Tasks." : "Loading project summary…")}</p>}
          </section>
          {insight}
        </div>
        <aside className="overview-aside" aria-label={t("Project context")}>
          <section className="overview-card overview-runtime">
            <div className="overview-card-heading"><h3>{t("Runtime health")}</h3><span className={`overview-state ${snapshot?.runtime.live ? "state-done" : ""}`}>{snapshot ? t(snapshot.runtime.live ? "Live" : "Offline") : "—"}</span></div>
            {pulse}
            <details className="overview-runtime-details"><summary>{t("Runtime details")}</summary><dl>
              <dt>{t("Snapshot")}</dt><dd>{snapshot?.seq ?? "—"}</dd>
              <dt>{t("Actions")}</dt><dd>{snapshot ? t(snapshot.runtime.actions?.mutation_enabled ? snapshot.runtime.actions.requires_token ? "Unlock required" : "Available" : "Read only") : "—"}</dd>
            </dl></details>
          </section>
          <section className="overview-card overview-distribution">
            <div className="overview-card-heading"><h3>{t("Tasks by status")}</h3><span className="muted">{snapshot ? total : "—"}</span></div>
            <div className="overview-status-bar" aria-hidden="true">{counts.map((item) => <i key={item.id} className={`status-${item.id}`} style={{ flex: item.count }} />)}</div>
            {counts.map((item) => <button type="button" className="overview-status-row" key={item.id} onClick={() => onOpenPage("board")}><i className={`status-${item.id}`} aria-hidden="true" /><span>{t(item.title)}</span><strong>{snapshot ? item.count : "—"}</strong></button>)}
          </section>
          <section className="overview-card overview-activity">
            <div className="overview-card-heading"><h3>{t("Recent activity")}</h3><button type="button" onClick={() => onOpenPage("traces")}>{t("View all")} <ArrowUpRight size={13} aria-hidden="true" /></button></div>
            {recent.length ? recent.map((event, index) => <div className="overview-activity-row" key={index}><span><strong>{String(event.type || "Event").replaceAll(".", " · ")}</strong><small>{String(event.task_id || event.actor || "")}</small></span><time>{formatTime(String(event.ts || ""))}</time></div>) : <p className="overview-card-empty">{t(activityStatus)}</p>}
          </section>
        </aside>
      </div>
    </section>
  );
}
