// ProjectionPage + exclusive closure, extracted verbatim from App.tsx (P1 split).
import type { ActionResponse, AgentSummary, ChannelSummary, DeliveryFeaturesPage, EventsPage, Feature, IntegrationQueueProjection, RecentEvent, RepairActionProjection, SearchResult, Snapshot, TraceSummary } from "../../api/types";
import { LoopPageV2 } from "../../components/delivery-trace/LoopPageV2";
import { DeliveryTracePage } from "../../components/delivery-trace/DeliveryTracePage";
import { Settings } from "lucide-react";
import { useEffect, useState } from "react";
import type { LiveState, PageId, ProjectionKind, ThemeMode } from "../../app/sharedTypes";
import { KeyValuePanel, ProjectionEmptyState, eventKey, isObservabilityPage } from "../../app/shared";
import { AgentViewPage } from "../agent-view/AgentViewPage";
import { ObservabilityPage } from "../observability/ObservabilityPage";
import { AutomationsPage } from "../automations/AutomationsPage";
import { getProjectAutomations, getProjectTraces } from "../../api/client";
import { GoalCoveragePage } from "../goal-coverage/GoalCoveragePage";

export function ProjectionPage({
  actionReady,
  actionState,
  activeProjectId,
  channels,
  deliveryFeaturesPage,
  eventsPage,
  eventFilter,
  integrationQueue,
  liveState,
  recentEvents,
  page,
  projectionDetail,
  repairActions,
  searchResult,
  selectedTaskId,
  setEventFilter,
  snapshot,
  themeMode,
  onAction,
  onAddAgentToChannel,
  onClearTaskScope,
  onOpenChannel,
  onOpenPage,
  onThemeModeChange,
  onOpenProjection,
  onSelectTask,
}: {
  actionReady: boolean;
  actionState: string;
  activeProjectId: string;
  channels: ChannelSummary[];
  deliveryFeaturesPage: DeliveryFeaturesPage | null;
  eventsPage: EventsPage | null;
  eventFilter: string;
  integrationQueue: IntegrationQueueProjection | null;
  liveState: LiveState;
  recentEvents: RecentEvent[];
  page: PageId;
  projectionDetail: Record<string, unknown> | null;
  repairActions: RepairActionProjection | null;
  searchResult: SearchResult | null;
  selectedTaskId: string | null;
  setEventFilter: (value: string) => void;
  snapshot: Snapshot | null;
  themeMode: ThemeMode;
  onAction: (action: string, payload: Record<string, unknown>) => Promise<ActionResponse>;
  onAddAgentToChannel: (agent: AgentSummary) => void;
  onClearTaskScope: () => void;
  onOpenChannel: (channelId: string) => void;
  onOpenPage: (page: PageId) => void;
  onThemeModeChange: (mode: ThemeMode) => void;
  onOpenProjection: (kind: ProjectionKind, id: string) => void;
  onSelectTask: (taskId: string) => void;
}) {
  const eventItems = eventsPage?.items ?? [];
  const [selectedEventKey, setSelectedEventKey] = useState("");
  useEffect(() => {
    if (!isObservabilityPage(page)) return;
    if (eventItems.length === 0) {
      if (selectedEventKey) setSelectedEventKey("");
      return;
    }
    if (selectedEventKey && eventItems.some((event, index) => eventKey(event, index) === selectedEventKey)) return;
    setSelectedEventKey(eventKey(eventItems[0], 0));
  }, [eventItems, page, selectedEventKey]);
  const selectedEvent = eventItems.find((event, index) => eventKey(event, index) === selectedEventKey) ?? eventItems[0] ?? null;

  // Automations data comes from the scoped /automations endpoint, not the
  // snapshot: this page loads the light snapshot (automations ∉
  // isObservabilityPage) where snapshot.automations is empty.
  const [automationData, setAutomationData] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    if (page !== "automations" || !activeProjectId) return undefined;
    let cancelled = false;
    void getProjectAutomations(activeProjectId)
      .then((data) => { if (!cancelled) setAutomationData(data); })
      .catch(() => { if (!cancelled) setAutomationData(null); });
    return () => { cancelled = true; };
  }, [activeProjectId, page]);

  // traces: the page loads the light snapshot (traces ∉ isObservabilityPage) and
  // fetches the scoped /traces roll-up instead of the slow full snapshot.
  const [traceRows, setTraceRows] = useState<TraceSummary[] | null>(null);
  useEffect(() => {
    if (page !== "traces" || !activeProjectId) return undefined;
    let cancelled = false;
    void getProjectTraces(activeProjectId)
      .then((data) => {
        if (cancelled) return;
        const rows = Array.isArray((data as { traces?: unknown }).traces)
          ? ((data as { traces: TraceSummary[] }).traces)
          : [];
        setTraceRows(rows);
      })
      .catch(() => { if (!cancelled) setTraceRows(null); });
    return () => { cancelled = true; };
  }, [activeProjectId, page]);

  // 保留完整 Feature 对象(含 title/source):选择器要按 source 区分真
  // feature 与 fallback:trace-ref 升格的运维/子流 trace(racing 评审)。
  const deliveryFeatureById = new Map<string, Feature>();
  for (const feature of [
    ...(deliveryFeaturesPage?.delivery_features ?? []),
    ...(deliveryFeaturesPage?.features ?? []),
    ...(snapshot?.delivery_features ?? []),
    ...(snapshot?.features ?? []),
  ]) {
    if (!deliveryFeatureById.has(feature.id)) deliveryFeatureById.set(feature.id, feature);
  }
  const deliveryFeatures = [...deliveryFeatureById.values()];

  // PM 快赢:总成本进 hero(snapshot 已在手,零额外请求)。
  const usageByRole = (snapshot?.agent_live as Record<string, unknown> | undefined)?.usage_by_role;
  const deliveryTotalUsd = Object.values(
    (usageByRole && typeof usageByRole === "object" ? usageByRole : {}) as Record<string, { usd?: number }>,
  ).reduce((sum, role) => sum + (typeof role?.usd === "number" ? role.usd : 0), 0);

  if (page === "goal-coverage") {
    return (
      <GoalCoveragePage
        features={deliveryFeatures}
        onSelectTask={onSelectTask}
        projectId={activeProjectId}
      />
    );
  }

  if (page === "delivery" || page === "delivery-trace" || page === "delivery-graph") {
    return (
      <DeliveryTracePage
          onOpenPage={onOpenPage}
          onSelectTask={onSelectTask}
          projectId={activeProjectId}
          totalUsd={deliveryTotalUsd}
          features={deliveryFeatures}
          liveEvents={recentEvents}
          mode={page === "delivery-trace" ? "trace" : page === "delivery-graph" ? "graph" : "overview"}
        />
    );
  }

  if (page === "behavior-loop") {
    return <LoopPageV2 projectId={activeProjectId} />;
  }

  if (isObservabilityPage(page) || page === "traces" || page === "diagnostics") {
    return (
      <ObservabilityPage
        activePage={page}
        activeProjectId={activeProjectId}
        eventFilter={eventFilter}
        eventItems={eventItems}
        eventsPage={eventsPage}
        integrationQueue={integrationQueue}
        liveState={liveState}
        onOpenChannel={onOpenChannel}
        onOpenPage={onOpenPage}
        onOpenProjection={onOpenProjection}
        onSelectEvent={setSelectedEventKey}
        projectionDetail={projectionDetail}
        repairActions={repairActions}
        searchResult={searchResult}
        selectedEvent={selectedEvent}
        selectedEventKey={selectedEvent ? eventKey(selectedEvent, eventItems.indexOf(selectedEvent)) : ""}
        setEventFilter={setEventFilter}
        snapshot={snapshot}
        scopedTraceRows={page === "traces" ? traceRows : null}
      />
    );
  }

  if (page === "agents") {
    return (
      <AgentViewPage
        actionReady={actionReady}
        agentCockpit={snapshot?.agent_cockpit ?? null}
        agentLive={snapshot?.agent_live ?? null}
        agents={snapshot?.agents ?? []}
        assignmentRoutes={snapshot?.assignment_routes ?? null}
        channels={channels.length ? channels : snapshot?.channels ?? []}
        cost={snapshot?.cost ?? null}
        executionPatterns={snapshot?.execution_patterns ?? null}
        fleetStats={snapshot?.fleet_stats ?? null}
        metricsSnapshot={snapshot?.metrics_snapshot ?? null}
        onAddAgentToChannel={onAddAgentToChannel}
        onAction={onAction}
        onSelectTask={onSelectTask}
        projectId={activeProjectId}
        providerCapabilities={snapshot?.provider_capabilities ?? null}
        recovery={snapshot?.recovery ?? null}
      />
    );
  }
  if (page === "automations") {
    return (
      <AutomationsPage
        actionReady={actionReady}
        actionState={actionState}
        automations={automationData}
        onRun={(automationId) => onAction("automation-run", {
          automation_id: automationId,
          source: "web-automations-page",
          trigger: "manual",
        })}
      />
    );
  }
  if (page === "backlogs") {
    const rows = [
      { key: "candidate_dir", value: "backlogs/ (local proposed/defer)" },
      { key: "sprint_dir", value: "tasks/ (active/done tracked archive)" },
      { key: "status", value: "planning projection only" },
    ];
    return (
      <>
        <div className="section-heading">
          <div>
            <h2>Backlogs</h2>
            <span className="muted">planning drift and sprint boundary</span>
          </div>
        </div>
        <KeyValuePanel title="Backlog Boundary" rows={rows} />
      </>
    );
  }
  if (page === "settings") {
    const themeOptions: Array<{ id: ThemeMode; label: string }> = [
      { id: "light", label: "Light" },
      { id: "dark", label: "Dark" },
      { id: "system", label: "System" },
    ];
    const runtimeRows = [
      { key: "control_plane", value: "zf.yaml" },
      { key: "runtime_truth", value: "TaskStore/EventWriter" },
      { key: "web_mutation", value: snapshot?.runtime.actions?.mutation_enabled ? "token/session gated" : "read-only" },
      { key: "project_state_dir", value: snapshot?.project.state_dir ?? "-" },
    ];
    return (
      <>
        <div className="section-heading">
          <div>
            <h2>Settings</h2>
            <span className="muted">appearance and local web preferences</span>
          </div>
        </div>
        <div className="settings-grid">
          <section className="subsection settings-panel">
            <div className="inline-heading">
              <h3>Appearance</h3>
              <span className="muted">Theme</span>
            </div>
            <div className="theme-options" role="radiogroup" aria-label="Theme">
              {themeOptions.map((option) => (
                <button
                  className={`theme-option ${themeMode === option.id ? "active" : ""}`}
                  key={option.id}
                  role="radio"
                  aria-checked={themeMode === option.id}
                  aria-label={`Select ${option.label} theme`}
                  type="button"
                  onClick={() => onThemeModeChange(option.id)}
                >
                  <ThemePreview mode={option.id} />
                  <span className="theme-option-label">{option.label}</span>
                </button>
              ))}
            </div>
          </section>
          <KeyValuePanel title="Runtime Boundary" rows={runtimeRows} />
        </div>
      </>
    );
  }
  return (
    <ProjectionEmptyState
      state={{
        title: "Page unavailable",
        description: "This compatibility route no longer owns a standalone product view.",
        icon: Settings,
      }}
    />
  );
}


function ThemePreview({ mode }: { mode: ThemeMode }) {
  if (mode === "system") {
    return (
      <span className="theme-preview" aria-hidden="true">
        <WindowMockup variant="light" />
        <span className="theme-system-dark">
          <WindowMockup variant="dark" />
        </span>
      </span>
    );
  }
  return (
    <span className="theme-preview" aria-hidden="true">
      <WindowMockup variant={mode} />
    </span>
  );
}


function WindowMockup({ variant }: { variant: "light" | "dark" }) {
  return (
    <span className={`theme-window theme-window-${variant}`}>
      <span className="theme-titlebar">
        <span />
        <span />
        <span />
      </span>
      <span className="theme-window-body">
        <span className="theme-window-sidebar">
          <i />
          <i />
        </span>
        <span className="theme-window-content">
          <i />
          <i />
          <i />
        </span>
      </span>
    </span>
  );
}
