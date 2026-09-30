import { useSettingsPreferences } from "../../settings/useSettingsPreferences";
import { uiText } from "../../settings/uiText";
// ProjectionPage + exclusive closure, extracted verbatim from App.tsx (P1 split).
import type { ActionResponse, AgentSummary, ChannelSummary, DeliveryFeaturesPage, EventsPage, Feature, IntegrationQueueProjection, RecentEvent, RepairActionProjection, SearchResult, Snapshot, TaskPipelineProjection } from "../../api/types";
import { DeliveryTracePage } from "../../components/delivery-trace/DeliveryTracePage";
import { Settings } from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";
import type { LiveState, PageId, ProjectionKind, ThemeMode } from "../../app/sharedTypes";
import { KeyValuePanel, ProjectionEmptyState, eventKey, isObservabilityPage } from "../../app/shared";
import { AgentViewPage } from "../agent-view/AgentViewPage";
import { ObservabilityPage } from "../observability/ObservabilityPage";
import { AutomationsPage } from "../automations/AutomationsPage";
import { getProjectAutomations } from "../../api/client";
import { WorkflowProposalPage } from "../workflow/WorkflowProposalPage";
import { SettingsPage } from "../../settings/SettingsPage";

const LazyLoopPageV2 = lazy(() => import("../../components/delivery-trace/LoopPageV2").then((module) => ({
  default: module.LoopPageV2,
})));

export function ProjectionPage({
  actionReady,
  actionState,
  activeProjectId,
  channels,
  deliveryFeaturesPage,
  eventsPage,
  eventFilter,
  integrationQueue,
  taskPipeline,
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
  taskPipeline: TaskPipelineProjection | null;
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
  useSettingsPreferences();
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

  if (page === "workflows") {
    return (
      <WorkflowProposalPage
        actionReady={actionReady}
        actionState={actionState}
        onAction={onAction}
        onOpenProjection={onOpenProjection}
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
          liveState={liveState}
          mode={page === "delivery-trace" ? "trace" : page === "delivery-graph" ? "graph" : "overview"}
        />
    );
  }

  if (page === "behavior-loop") {
    return (
      <Suspense fallback={<div className="loop-v2-page loop-v2-state">{uiText("Loading loop view…")}</div>}>
        <LazyLoopPageV2 liveEvents={recentEvents} liveState={liveState} projectId={activeProjectId} />
      </Suspense>
    );
  }

  if (isObservabilityPage(page) || page === "diagnostics") {
    return (
      <ObservabilityPage
        activePage={page}
        activeProjectId={activeProjectId}
        eventFilter={eventFilter}
        eventItems={eventItems}
        eventsPage={eventsPage}
        integrationQueue={integrationQueue}
        taskPipeline={taskPipeline}
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
            <h2>{uiText("Backlogs")}</h2>
            <span className="muted">planning drift and sprint boundary</span>
          </div>
        </div>
        <KeyValuePanel title="Backlog Boundary" rows={rows} />
      </>
    );
  }
  if (page === "settings") {
    return <SettingsPage key={activeProjectId} projectId={activeProjectId} actionReady={actionReady}
      themeMode={themeMode} onThemeModeChange={onThemeModeChange} snapshot={snapshot}/>;
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
