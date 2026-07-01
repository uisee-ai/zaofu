// ProjectionPage + exclusive closure, extracted verbatim from App.tsx (P1 split).
import type { ActionResponse, AgentSummary, ChannelSummary, DeliveryFeaturesPage, EventsPage, IntegrationQueueProjection, RecentEvent, RepairActionProjection, SearchResult, Snapshot, Task, TraceSummary, WorkdirSummary } from "../../api/types";
import { BehaviorLoopPage } from "../../components/delivery-trace/BehaviorLoopPage";
import { DeliveryTracePage } from "../../components/delivery-trace/DeliveryTracePage";
import { Archive, Bell, Boxes, FileText, FolderGit2, GitFork, ListTodo, Map as MapIcon, PlayCircle, Radio, Route, Settings, Users } from "lucide-react";
import { useEffect, useState } from "react";
import type { EmptyStateSpec, LiveState, PageId, ProjectionKind, ProjectionMetricSpec, ThemeMode, UiTone } from "../../app/sharedTypes";
import { KeyValuePanel, ProjectionEmptyState, ProjectionList, ProjectionMetricGrid, TablePage, TraceDetailPanel, TraceIndexList, asRecord, compactPath, eventKey, isObservabilityPage, textValue } from "../../app/shared";
import { AgentViewPage } from "../agent-view/AgentViewPage";
import { ObservabilityPage } from "../observability/ObservabilityPage";
import { AutomationsPage } from "../automations/AutomationsPage";
import { getProjectAutomations, getProjectTraces } from "../../api/client";
import { SkillsPage } from "../skills/SkillsPage";
import { RuntimePanel } from "../runtime/RuntimePanel";

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

  const deliveryFeatureIds = Array.from(new Set([
    ...((deliveryFeaturesPage?.delivery_features ?? []).map((feature) => feature.id)),
    ...((deliveryFeaturesPage?.features ?? []).map((feature) => feature.id)),
    ...((snapshot?.delivery_features ?? []).map((feature) => feature.id)),
    ...((snapshot?.features ?? []).map((feature) => feature.id)),
  ]));

  if (page === "delivery" || page === "delivery-trace" || page === "delivery-graph") {
    return (
      <DeliveryTracePage
        onOpenPage={onOpenPage}
        projectId={activeProjectId}
        featureIds={deliveryFeatureIds}
        liveEvents={recentEvents}
        mode={page === "delivery-trace" ? "trace" : page === "delivery-graph" ? "graph" : "overview"}
      />
    );
  }

  if (page === "behavior-loop") {
    return (
      <BehaviorLoopPage
        projectId={activeProjectId}
        featureIds={deliveryFeatureIds}
        onOpenTrace={(traceId) => onOpenProjection("trace", traceId)}
        onSelectTask={onSelectTask}
      />
    );
  }

  if (isObservabilityPage(page)) {
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
  if (page === "workdirs") {
    return (
      <WorkdirsPage
        rows={snapshot?.workdirs ?? []}
        snapshotReady={Boolean(snapshot)}
        onOpenPage={onOpenPage}
      />
    );
  }
  if (page === "skills") {
    return <SkillsPage summary={snapshot?.skills ?? null} />;
  }
  if (page === "traces") {
    return (
      <TraceExplorerPage
        rows={traceRows ?? snapshot?.traces ?? []}
        snapshotReady={Boolean(snapshot) || traceRows !== null}
        detail={projectionDetail}
        onOpen={(id) => onOpenProjection("trace", id)}
        onOpenPage={onOpenPage}
      />
    );
  }
  if (page === "candidates") {
    return (
      <ProjectionList
        emptyState={{
          title: "No candidates yet",
          description: "Candidate projections appear after writer fanout, integration, or ship-candidate events are recorded.",
          icon: Boxes,
          actions: [{ label: "Open Fanouts", onClick: () => onOpenPage("fanouts") }],
        }}
        title="Candidates"
        rows={snapshot?.candidates ?? []}
        idKey="pdd_id"
        onOpen={(id) => onOpenProjection("candidate", id)}
        detail={projectionDetail}
      />
    );
  }
  if (page === "fanouts") {
    return (
      <ProjectionList
        emptyState={{
          title: "No fanout executions yet",
          description: "Fanout lanes appear after task_map.ready, STAR fanout dispatch, or workflow fan-out events.",
          icon: GitFork,
          actions: [{ label: "Open Tasks", onClick: () => onOpenPage("board") }],
        }}
        title="Fanouts"
        rows={snapshot?.fanouts ?? []}
        idKey="fanout_id"
        onOpen={(id) => onOpenProjection("fanout", id)}
        detail={projectionDetail}
      />
    );
  }
  if (page === "runs") {
    const activeRows = (snapshot?.active_runs ?? []).map((run) => ({
      ...run,
      projection: "active",
    }));
    const archivedRows = (snapshot?.runs ?? []).map((run) => ({
      ...run,
      projection: "archived",
    }));
    return (
      <ProjectionList
        emptyState={{
          title: "No workflow runs yet",
          description: "Runs appear after zf start, workflow invoke events, or archived runtime execution records.",
          icon: PlayCircle,
          actions: [
            { label: "Open Events", onClick: () => onOpenPage("events") },
            { label: "Open Tasks", onClick: () => onOpenPage("board") },
          ],
        }}
        title="Runs"
        rows={[...activeRows, ...archivedRows]}
        idKey="run_id"
        onOpen={(id) => onOpenProjection("run", id)}
        detail={projectionDetail}
      />
    );
  }
  if (page === "archives") {
    return (
      <ArchivesPage
        rows={snapshot?.archive_tasks ?? []}
        snapshotReady={Boolean(snapshot)}
        onOpenPage={onOpenPage}
        onSelectTask={onSelectTask}
      />
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
    <RuntimePanel
      actionReady={actionReady}
      actionState={actionState}
      activeProjectId={activeProjectId}
      snapshot={snapshot}
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


function TraceExplorerPage({
  detail,
  onOpen,
  onOpenPage,
  rows,
  snapshotReady,
}: {
  detail: Record<string, unknown> | null;
  onOpen: (id: string) => void;
  onOpenPage: (page: PageId) => void;
  rows: TraceSummary[];
  snapshotReady: boolean;
}) {
  const taskIds = new Set(rows.flatMap((row) => row.task_ids ?? []));
  const actors = new Set(rows.flatMap((row) => row.actors ?? []));
  const totalEvents = rows.reduce((total, row) => total + Number(row.event_count || 0), 0);
  const latest = [...rows].sort((left, right) => Number(right.last_seq || 0) - Number(left.last_seq || 0))[0];
  const metrics: ProjectionMetricSpec[] = [
    { icon: MapIcon, label: "Traces", value: snapshotReady ? rows.length : "-", meta: snapshotReady ? "causation chains" : "snapshot pending", tone: rows.length ? "info" : "muted" },
    { icon: Radio, label: "Events", value: snapshotReady ? totalEvents : "-", meta: "linked events", tone: totalEvents ? "info" : "muted" },
    { icon: ListTodo, label: "Tasks", value: snapshotReady ? taskIds.size : "-", meta: "referenced tasks", tone: taskIds.size ? "info" : "muted" },
    { icon: Users, label: "Actors", value: snapshotReady ? actors.size : "-", meta: latest?.last_type || (snapshotReady ? "no latest event" : "waiting for snapshot"), tone: actors.size ? "info" : "muted" },
  ];
  const emptyState: EmptyStateSpec = {
    title: "No event traces selected",
    description: "Trace projections appear after task, workflow, or runtime events record a causation chain.",
    icon: MapIcon,
    actions: [
      { label: "Open Events", onClick: () => onOpenPage("events") },
      { label: "Open Runs", onClick: () => onOpenPage("runs") },
    ],
  };

  return (
    <div className="projection-page-shell trace-page-shell">
      <div className="section-heading projection-page-heading">
        <div>
          <h2>Event Traces</h2>
          <span className="muted">causation chains, task lenses, and workflow route previews</span>
        </div>
        <span className="metric-chip">{snapshotReady ? `${rows.length} traces` : "snapshot pending"}</span>
      </div>
      <ProjectionMetricGrid metrics={metrics} />
      {!snapshotReady ? (
        <section className="subsection projection-landing-empty">
          <ProjectionEmptyState
            state={{
              title: "Trace snapshot pending",
              description: "Trace Index will appear after the project snapshot finishes loading.",
              icon: MapIcon,
            }}
          />
        </section>
      ) : rows.length === 0 ? (
        <section className="subsection projection-landing-empty">
          <ProjectionEmptyState state={emptyState} />
        </section>
      ) : (
        <div className="trace-explorer-grid">
          <section className="subsection projection-list-panel">
            <TraceIndexList
              rows={rows}
              onOpen={onOpen}
            />
          </section>
          <section className="subsection projection-preview-panel">
            <div className="inline-heading">
              <h3>Trace Preview</h3>
              <span className="muted">{detail ? textValue(detail.trace_id) || "selected" : "select a trace"}</span>
            </div>
            {detail ? (
              <TraceDetailPanel detail={detail} />
            ) : (
              <ProjectionEmptyState
                state={{
                  title: "Select a trace",
                  description: "Open a trace row to inspect its route, actors, source events, and task links.",
                  icon: MapIcon,
                  compact: true,
                }}
              />
            )}
          </section>
        </div>
      )}
    </div>
  );
}


function WorkdirsPage({
  onOpenPage,
  rows,
  snapshotReady,
}: {
  onOpenPage: (page: PageId) => void;
  rows: WorkdirSummary[];
  snapshotReady: boolean;
}) {
  const records = rows.map((row) => asRecord(row));
  const dirty = records.filter((row) => Boolean(row.dirty)).length;
  const missing = records.filter((row) => row.exists === false || row.project_exists === false || textValue(row.error)).length;
  const assigned = records.filter((row) => textValue(row.active_task) || textValue(row.task_id)).length;
  const linkedEvents = records.reduce((total, row) => total + (Array.isArray(row.linked_events) ? row.linked_events.length : 0), 0);
  const metrics: ProjectionMetricSpec[] = [
    { icon: FolderGit2, label: "Workdirs", value: snapshotReady ? rows.length : "-", meta: snapshotReady ? "runtime worktrees" : "snapshot pending", tone: rows.length ? "info" : "muted" },
    { icon: ListTodo, label: "Assigned", value: snapshotReady ? assigned : "-", meta: "task-bound dirs", tone: assigned ? "info" : "muted" },
    { icon: GitFork, label: "Dirty", value: snapshotReady ? dirty : "-", meta: "needs review", tone: dirty ? "warn" : "ok" },
    { icon: Bell, label: "Missing", value: snapshotReady ? missing : "-", meta: "path or project drift", tone: missing ? "err" : "ok" },
    { icon: Radio, label: "Events", value: snapshotReady ? linkedEvents : "-", meta: "linked signals", tone: linkedEvents ? "info" : "muted" },
  ];
  const emptyState: EmptyStateSpec = {
    title: "No workdirs yet",
    description: "Worker workdirs appear after runtime roles are spawned for this project.",
    icon: FolderGit2,
    actions: [{ label: "Open Runtime", onClick: () => onOpenPage("runtime") }],
  };

  return (
    <div className="projection-page-shell workdirs-page-shell">
      <div className="section-heading projection-page-heading">
        <div>
          <h2>Workdirs</h2>
          <span className="muted">worker worktree health, ownership, and drift signals</span>
        </div>
        <span className="metric-chip">{snapshotReady ? `${rows.length} directories` : "snapshot pending"}</span>
      </div>
      <ProjectionMetricGrid metrics={metrics} />
      {!snapshotReady ? (
        <section className="subsection projection-landing-empty">
          <ProjectionEmptyState
            state={{
              title: "Workdir snapshot pending",
              description: "Workdir health will appear after the project snapshot finishes loading.",
              icon: FolderGit2,
            }}
          />
        </section>
      ) : rows.length === 0 ? (
        <section className="subsection projection-landing-empty">
          <ProjectionEmptyState state={emptyState} />
        </section>
      ) : (
        <>
          <section className="subsection">
            <div className="inline-heading">
              <h3>Workdir Health</h3>
              <span className="muted">{rows.length} runtime dirs</span>
            </div>
            <div className="workdir-card-grid">
              {records.slice(0, 12).map((row, index) => {
                const status = workdirStatusLabel(row);
                const instanceId = textValue(row.instance_id) || `workdir-${index + 1}`;
                return (
                  <article className={`workdir-card tone-${projectionStatusTone(status)}`} key={instanceId}>
                    <div className="workdir-card-head">
                      <span className={`badge badge-${projectionStatusTone(status)}`}>{status}</span>
                      <strong>{textValue(row.role_name) || textValue(row.role_kind) || instanceId}</strong>
                    </div>
                    <p title={textValue(row.workdir)}>{compactPath(textValue(row.workdir) || textValue(row.project_path) || "-")}</p>
                    <div className="workdir-card-meta">
                      <span className="mono">{textValue(row.branch) || textValue(row.branch_or_ref) || "-"}</span>
                      {textValue(row.active_task) ? <span>{textValue(row.active_task)}</span> : null}
                      {Array.isArray(row.linked_events) ? <span>{row.linked_events.length} events</span> : null}
                    </div>
                  </article>
                );
              })}
            </div>
          </section>
          <TablePage title="Workdir Ledger" rows={rows} embedded emptyState={{ ...emptyState, compact: true }} />
        </>
      )}
    </div>
  );
}


function ArchivesPage({
  onOpenPage,
  onSelectTask,
  rows,
  snapshotReady,
}: {
  onOpenPage: (page: PageId) => void;
  onSelectTask: (taskId: string) => void;
  rows: Task[];
  snapshotReady: boolean;
}) {
  const records = rows.map((row) => asRecord(row));
  const done = records.filter((row) => projectionStatusTone(textValue(row.status)) === "ok").length;
  const withEvidence = records.filter((row) => Array.isArray(row.evidence_badges) && row.evidence_badges.length > 0).length;
  const phases = new Set(records.map((row) => textValue(row.workflow_phase)).filter(Boolean));
  const latest = records[0];
  const metrics: ProjectionMetricSpec[] = [
    { icon: Archive, label: "Archived", value: snapshotReady ? rows.length : "-", meta: snapshotReady ? "closed task records" : "snapshot pending", tone: rows.length ? "info" : "muted" },
    { icon: ListTodo, label: "Done", value: snapshotReady ? done : "-", meta: "terminal tasks", tone: done ? "ok" : "muted" },
    { icon: FileText, label: "Evidence", value: snapshotReady ? withEvidence : "-", meta: "badge-backed tasks", tone: withEvidence ? "info" : "muted" },
    { icon: Route, label: "Phases", value: snapshotReady ? phases.size : "-", meta: latest ? textValue(latest.status) || "latest archive" : snapshotReady ? "no archive yet" : "waiting for snapshot", tone: phases.size ? "info" : "muted" },
  ];
  const emptyState: EmptyStateSpec = {
    title: "No archived tasks",
    description: "Completed task archives appear after kernel-managed task closeout.",
    icon: Archive,
    actions: [{ label: "Open Tasks", onClick: () => onOpenPage("board") }],
  };

  return (
    <div className="projection-page-shell archives-page-shell">
      <div className="section-heading projection-page-heading">
        <div>
          <h2>Archives</h2>
          <span className="muted">closed tasks, evidence trail, and done-state ledger</span>
        </div>
        <span className="metric-chip">{snapshotReady ? `${rows.length} archived` : "snapshot pending"}</span>
      </div>
      <ProjectionMetricGrid metrics={metrics} />
      {!snapshotReady ? (
        <section className="subsection projection-landing-empty">
          <ProjectionEmptyState
            state={{
              title: "Archive snapshot pending",
              description: "Archive records will appear after the project snapshot finishes loading.",
              icon: Archive,
            }}
          />
        </section>
      ) : rows.length === 0 ? (
        <section className="subsection projection-landing-empty">
          <ProjectionEmptyState state={emptyState} />
        </section>
      ) : (
        <>
          <section className="subsection">
            <div className="inline-heading">
              <h3>Recent Archives</h3>
              <span className="muted">{rows.length} tasks</span>
            </div>
            <div className="archive-card-grid">
              {records.slice(0, 10).map((row, index) => {
                const taskId = textValue(row.id) || `archive-${index + 1}`;
                const status = textValue(row.status) || "archived";
                return (
                  <button className="archive-card" key={taskId} type="button" onClick={() => onSelectTask(taskId)}>
                    <div className="archive-card-head">
                      <span className={`badge badge-${projectionStatusTone(status)}`}>{status}</span>
                      <span className="mono">{taskId}</span>
                    </div>
                    <strong>{textValue(row.title) || "Archived task"}</strong>
                    <div className="archive-card-meta">
                      {textValue(row.workflow_phase) ? <span>{textValue(row.workflow_phase)}</span> : null}
                      {textValue(row.assigned_to) ? <span>{textValue(row.assigned_to)}</span> : null}
                      {Array.isArray(row.evidence_badges) ? <span>{row.evidence_badges.length} evidence</span> : null}
                    </div>
                  </button>
                );
              })}
            </div>
          </section>
          <TablePage
            title="Archive Ledger"
            rows={rows}
            embedded
            onOpen={(row) => {
              const taskId = String(row.id ?? "");
              if (taskId) onSelectTask(taskId);
            }}
            emptyState={{ ...emptyState, compact: true }}
          />
        </>
      )}
    </div>
  );
}


function projectionStatusTone(value: string): UiTone {
  const normalized = value.toLowerCase();
  if (["ok", "ready", "clean", "done", "completed", "passed", "success", "running", "live"].includes(normalized)) return "ok";
  if (["assigned", "active", "in_progress", "streaming", "started"].includes(normalized)) return "info";
  if (["dirty", "pending", "paused", "skipped", "blocked", "warning", "warn"].includes(normalized)) return "warn";
  if (["missing", "failed", "error", "err", "drift", "stale"].includes(normalized)) return "err";
  return "muted";
}


function workdirStatusLabel(row: Record<string, unknown>): string {
  if (textValue(row.error)) return "error";
  if (row.exists === false || row.project_exists === false) return "missing";
  if (Boolean(row.dirty)) return "dirty";
  if (textValue(row.active_task) || textValue(row.task_id)) return "assigned";
  return "ready";
}
