// ObservabilityPage + exclusive closure, extracted verbatim from App.tsx (P1 split).
import { search } from "../../api/client";
import type { EventRecord, EventsPage, IntegrationQueueEntry, IntegrationQueueProjection, RepairActionProjection, RepairActionRecord, SearchResult, Snapshot, TraceSummary } from "../../api/types";
import { LogsPanel } from "../../components/observability/LogsPanel";
import { formatTokens } from "../../lib/format";
import { buildObservabilityEventWindow } from "../../app/observabilityModel";
import { Archive, Bell, Boxes, ChevronRight, FolderGit2, Gauge, GitFork, Map as MapIcon, PauseCircle, PlayCircle, Radio, SkipBack, SkipForward, Wrench, X } from "lucide-react";
import { Fragment, useEffect, useMemo, useState } from "react";
import type { LiveState, PageId, ParsedEventFilter, ProjectionKind, ProjectionMetricSpec, UiTone } from "../../app/sharedTypes";
import { EventTable, KeyValuePanel, PreBlock, ProjectionEmptyState, ProjectionList, ProjectionMetricGrid, TablePage, TraceDetailPanel, TraceIndexList, asRecord, asStringArray, eventChannelId, eventFamily, eventKey, eventPayload, eventSummary, formatUsd, parseEventFilter, stringify, textValue, truncateInline } from "../../app/shared";

type ObservabilityTab = "traces" | "events" | "logs" | "runs" | "fanouts" | "candidates" | "integration" | "repair" | "raw";

type TraceStatusFilter = "all" | "running" | "completed" | "failed" | "blocked" | "observed";

type TraceDurationFilter = "all" | "short" | "medium" | "long" | "unknown";

interface TraceFilters {
  query: string;
  status: TraceStatusFilter;
  role: string;
  backend: string;
  duration: TraceDurationFilter;
}

function observabilityTabForPage(page: PageId): ObservabilityTab {
  if (page === "events") return "events";
  if (page === "runs") return "runs";
  if (page === "fanouts") return "fanouts";
  if (page === "candidates") return "candidates";
  // Resources tab 已退役(operator 2026-07-11 整删):旧 workdirs/skills/
  // archives 深链落默认 traces。取证走 CLI(zf status/skills)或 Raw。
  if (page === "workdirs" || page === "skills" || page === "archives") return "traces";
  return "traces";
}


type EventFilterChip = {
  key: keyof Omit<ParsedEventFilter, "unknown"> | "query";
  label: string;
  value: string;
  fromScope?: boolean;
};


function removeEventFilterToken(value: string, key: string): string {
  return value
    .trim()
    .split(/\s+/)
    .filter((token) => token && !(token === key || token.startsWith(`${key}:`)))
    .join(" ");
}


function eventFilterChips(value: string, scopedTaskId: string): EventFilterChip[] {
  const parsed = parseEventFilter(value);
  const chips: EventFilterChip[] = [];
  const taskValue = parsed.task || scopedTaskId;
  if (taskValue) chips.push({ key: "task", label: "task", value: taskValue, fromScope: !parsed.task });
  if (parsed.actor) chips.push({ key: "actor", label: "actor", value: parsed.actor });
  if (parsed.type) chips.push({ key: "type", label: "type", value: parsed.type });
  if (parsed.prefix) chips.push({ key: "prefix", label: "prefix", value: parsed.prefix });
  if (parsed.failed) chips.push({ key: "failed", label: "failed", value: "true" });
  if (parsed.blocked) chips.push({ key: "blocked", label: "blocked", value: "true" });
  for (const token of parsed.unknown) chips.push({ key: "query", label: "query", value: token });
  return chips;
}


function eventPayloadHighlights(event: EventRecord | null): Array<[string, string]> {
  const payload = eventPayload(event);
  const keys = [
    "action",
    "status",
    "target",
    "target_ref",
    "message",
    "text",
    "reason",
    "pattern_id",
    "thread_id",
    "evidence_refs",
    "trace_id",
    "run_id",
  ];
  const rows: Array<[string, string]> = [];
  for (const key of keys) {
    const value = payload[key];
    if (value === undefined || value === null || value === "") continue;
    rows.push([key, truncateInline(stringify(value).replace(/\s+/g, " "), 140)]);
  }
  return rows.slice(0, 8);
}


function eventPrefixCount(events: EventRecord[], prefix: string): number {
  return events.filter((event) => event.type.startsWith(prefix)).length;
}


function eventText(event: EventRecord): string {
  return `${event.type} ${event.actor ?? ""} ${event.task_id ?? ""} ${stringify(eventPayload(event))}`.toLowerCase();
}


function eventMatchesToken(event: EventRecord, token: string): boolean {
  return eventText(event).includes(token.toLowerCase());
}


function eventFailureLike(event: EventRecord): boolean {
  return eventMatchesToken(event, "failed")
    || eventMatchesToken(event, "failure")
    || eventMatchesToken(event, "error")
    || eventMatchesToken(event, "rejected");
}


function eventBlockedLike(event: EventRecord): boolean {
  return eventMatchesToken(event, "blocked")
    || eventMatchesToken(event, "stuck")
    || eventMatchesToken(event, "action_required");
}


function semanticEventCount(events: EventRecord[], predicate: (event: EventRecord) => boolean): number {
  return events.filter(predicate).length;
}


export function ObservabilityPage({
  activePage,
  activeProjectId,
  eventFilter,
  eventItems,
  eventsPage,
  integrationQueue,
  liveState,
  onOpenChannel,
  onOpenPage,
  onOpenProjection,
  onSelectEvent,
  projectionDetail,
  repairActions,
  searchResult,
  selectedEvent,
  selectedEventKey,
  setEventFilter,
  snapshot,
}: {
  activePage: PageId;
  activeProjectId: string;
  eventFilter: string;
  eventItems: EventRecord[];
  eventsPage: EventsPage | null;
  integrationQueue: IntegrationQueueProjection | null;
  liveState: LiveState;
  onOpenChannel: (channelId: string) => void;
  onOpenPage: (page: PageId) => void;
  onOpenProjection: (kind: ProjectionKind, id: string) => void;
  onSelectEvent: (key: string) => void;
  projectionDetail: Record<string, unknown> | null;
  repairActions: RepairActionProjection | null;
  searchResult: SearchResult | null;
  selectedEvent: EventRecord | null;
  selectedEventKey: string;
  setEventFilter: (value: string) => void;
  snapshot: Snapshot | null;
}) {
  const [tab, setTab] = useState<ObservabilityTab>(() => readInitialObservabilityTab(activePage));
  const [traceFilters, setTraceFilters] = useState<TraceFilters>(() => readInitialTraceFilters());
  const [foldNoiseEvents, setFoldNoiseEvents] = useState(() => readInitialBooleanQuery("obs_fold_noise", true));
  const [autoFollowEvents, setAutoFollowEvents] = useState(() => readInitialBooleanQuery("obs_auto_follow", true));
  const [replayIndex, setReplayIndex] = useState(0);
  const [replayPlaying, setReplayPlaying] = useState(false);
  const [requestedTraceId, setRequestedTraceId] = useState("");
  const deepLinkTraceId = useMemo(() => readTraceExplorerDeepLink(), []);
  useEffect(() => {
    setTab(activePage === "observability" ? readInitialObservabilityTab(activePage) : observabilityTabForPage(activePage));
  }, [activePage]);
  const snapshotReady = Boolean(snapshot);
  const traces = snapshot?.traces ?? [];
  const runs = [...(snapshot?.active_runs ?? []), ...(snapshot?.runs ?? [])];
  const fanouts = snapshot?.fanouts ?? [];
  const candidates = snapshot?.candidates ?? [];
  const totalTokens = Object.values(snapshot?.cost.per_role ?? {}).reduce(
    (total, role) => total + role.input_tokens + role.output_tokens,
    0,
  );
  const totalCost = snapshot?.cost.total_usd ?? 0;
  const maxContext = (snapshot?.agents ?? []).reduce<number | null>((max, agent) => {
    if (typeof agent.context_usage_ratio !== "number") return max;
    return Math.max(max ?? 0, agent.context_usage_ratio);
  }, null);
  const parsedEventFilter = parseEventFilter(eventFilter);
  const activeEventChips = eventFilterChips(eventFilter, parsedEventFilter.task || "");
  const normalizedEventFilter = eventFilter.trim();
  const eventSavedViews = [
    {
      label: "All",
      filter: "",
      count: eventItems.length,
      description: "complete event stream",
      active: !normalizedEventFilter,
    },
    {
      label: "Failed",
      filter: "failed",
      count: semanticEventCount(eventItems, eventFailureLike),
      description: "failed, rejected, or error signals",
      active: parsedEventFilter.failed,
    },
    {
      label: "Blocked",
      filter: "blocked",
      count: semanticEventCount(eventItems, eventBlockedLike),
      description: "blocked, stuck, action required",
      active: parsedEventFilter.blocked,
    },
    {
      label: "Channel",
      filter: "prefix:channel.",
      count: eventPrefixCount(eventItems, "channel."),
      description: "channel messages and member replies",
      active: parsedEventFilter.prefix === "channel.",
    },
    {
      label: "Runtime",
      filter: "prefix:runtime.",
      count: eventPrefixCount(eventItems, "runtime."),
      description: "runtime lifecycle and action events",
      active: parsedEventFilter.prefix === "runtime.",
    },
    {
      label: "Kanban Agent",
      filter: "prefix:kanban.agent.",
      count: eventPrefixCount(eventItems, "kanban.agent."),
      description: "operator chat and headless turns",
      active: parsedEventFilter.prefix === "kanban.agent.",
    },
    {
      label: "Context",
      filter: "prefix:worker.context.",
      count: eventPrefixCount(eventItems, "worker.context."),
      description: "context compression and resume signals",
      active: parsedEventFilter.prefix === "worker.context.",
    },
    {
      label: "Gates",
      filter: "prefix:gate.",
      count: eventPrefixCount(eventItems, "gate."),
      description: "impl, verify, judge gate evidence",
      active: parsedEventFilter.prefix === "gate.",
    },
    {
      label: "Replan",
      filter: "prefix:replan.",
      count: eventPrefixCount(eventItems, "replan."),
      description: "contract drift and replan loops",
      active: parsedEventFilter.prefix === "replan.",
    },
    {
      label: "Autoresearch",
      filter: "prefix:autoresearch.",
      count: eventPrefixCount(eventItems, "autoresearch."),
      description: "self-repair and score/replay events",
      active: parsedEventFilter.prefix === "autoresearch.",
    },
  ];
  const mutationAuditCount = Array.isArray(snapshot?.mutation_audit?.entries) ? snapshot.mutation_audit.entries.length : 0;
  const worktreeAuditEntries = Array.isArray(snapshot?.worktree_drift?.entries) ? snapshot.worktree_drift.entries : [];
  const worktreeActionCount = worktreeAuditEntries.filter((row) => Boolean(asRecord(row).action_required)).length;
  const searchTraceRows = searchResult?.traces ?? [];
  const traceRows = searchTraceRows.length ? searchTraceRows : traces;
  const filteredTraceRows = useMemo(
    () => traceRows.filter((row) => traceMatchesFilters(row, eventItems, traceFilters)),
    [eventItems, traceFilters, traceRows],
  );
  const eventWindow = useMemo(
    () => buildObservabilityEventWindow(eventItems, { foldNoise: foldNoiseEvents, maxRows: 600 }),
    [eventItems, foldNoiseEvents],
  );
  const visibleEventItems = eventWindow.visibleEvents;
  const renderedEventItems = eventWindow.renderedEvents;
  const hiddenNoiseCount = eventWindow.hiddenNoiseCount;
  const truncatedEventCount = eventWindow.truncatedEventCount;
  const replayEvents = useMemo(() => visibleEventItems.slice().reverse(), [visibleEventItems]);
  const selectedTraceId = textValue(projectionDetail?.trace_id);
  const traceDetailReady = Boolean(selectedTraceId);
  useEffect(() => {
    if (tab !== "traces" || !deepLinkTraceId || selectedTraceId === deepLinkTraceId || requestedTraceId === deepLinkTraceId) return;
    setRequestedTraceId(deepLinkTraceId);
    onOpenProjection("trace", deepLinkTraceId);
  }, [deepLinkTraceId, onOpenProjection, requestedTraceId, selectedTraceId, tab]);
  const openTrace = (traceId: string) => {
    writeTraceExplorerDeepLink(traceId);
    onOpenProjection("trace", traceId);
  };
  const streamTone: UiTone = liveState === "live" ? "ok" : liveState === "connecting" || liveState === "reconnecting" ? "warn" : "err";
  const metrics: ProjectionMetricSpec[] = [
    { icon: MapIcon, label: "Traces", value: snapshotReady ? traces.length : "-", meta: "causation chains", tone: traces.length ? "info" : "muted" },
    { icon: Radio, label: "Events", value: eventsPage ? eventItems.length : "-", meta: `seq ${snapshot?.seq ?? "-"}`, tone: eventItems.length ? "info" : "muted" },
    { icon: PlayCircle, label: "Runs", value: snapshotReady ? runs.length : "-", meta: `${snapshot?.active_runs.length ?? 0} active`, tone: snapshot?.active_runs.length ? "warn" : runs.length ? "info" : "muted" },
    { icon: GitFork, label: "Fanouts", value: snapshotReady ? fanouts.length : "-", meta: "fan-out / fan-in", tone: fanouts.length ? "info" : "muted" },
    { icon: Gauge, label: "Context", value: maxContext == null ? "unknown" : `${Math.round(maxContext * 100)}%`, meta: `${snapshot?.agents?.length ?? 0} agents`, tone: maxContext == null ? "muted" : maxContext >= 0.9 ? "err" : maxContext >= 0.75 ? "warn" : "ok" },
    { icon: Boxes, label: "Tokens", value: formatTokens(totalTokens), meta: formatUsd(totalCost), tone: totalTokens ? "info" : "muted" },
  ];
  const tabs: Array<{ id: ObservabilityTab; label: string; count?: number }> = [
    { id: "traces", label: "Traces", count: traces.length },
    { id: "events", label: "Events", count: eventItems.length },
    { id: "logs", label: "Logs" },
    { id: "runs", label: "Runs", count: runs.length },
    { id: "fanouts", label: "Fanouts", count: fanouts.length },
    { id: "candidates", label: "Candidates", count: candidates.length },
    { id: "integration", label: "Integration", count: integrationQueue?.summary.total ?? 0 },
    { id: "repair", label: "Repair", count: repairActions?.summary.total ?? 0 },
    // Tokens/Context 与 Feedback tab 已退役(operator 2026-07-11):前者三重复
    // (顶部卡片 + Agents 页 FLEET 逐行同数据),后者的家是 Inbox(可行动)与
    // Agents Attention Queue。同数据不二渲染。
    { id: "raw", label: "Raw" },
  ];

  function openTab(next: ObservabilityTab) {
    setTab(next);
    if (activePage !== "observability") onOpenPage("observability");
  }

  function selectEventFromDisplay(event: EventRecord) {
    const index = eventItems.indexOf(event);
    if (index >= 0) onSelectEvent(eventKey(event, index));
  }

  function selectReplay(nextIndex: number) {
    const bounded = Math.max(0, Math.min(nextIndex, Math.max(0, replayEvents.length - 1)));
    setReplayIndex(bounded);
    const event = replayEvents[bounded];
    if (event) selectEventFromDisplay(event);
  }

  useEffect(() => {
    persistObservabilityQuery(tab, traceFilters, {
      autoFollow: autoFollowEvents,
      foldNoise: foldNoiseEvents,
    });
  }, [autoFollowEvents, foldNoiseEvents, tab, traceFilters]);

  useEffect(() => {
    if (!autoFollowEvents || !visibleEventItems.length) return;
    selectEventFromDisplay(visibleEventItems[0]);
    setReplayIndex(Math.max(0, replayEvents.length - 1));
  }, [autoFollowEvents, replayEvents.length, visibleEventItems]);

  useEffect(() => {
    if (!replayPlaying) return;
    if (replayIndex >= replayEvents.length - 1) {
      setReplayPlaying(false);
      return;
    }
    const timer = window.setTimeout(() => selectReplay(replayIndex + 1), 650);
    return () => window.clearTimeout(timer);
  }, [replayIndex, replayEvents.length, replayPlaying]);

  useEffect(() => {
    if (replayIndex < replayEvents.length) return;
    setReplayIndex(Math.max(0, replayEvents.length - 1));
  }, [replayEvents.length, replayIndex]);

  const renderedSelectedEventKey =
    selectedEvent && renderedEventItems.includes(selectedEvent)
      ? eventKey(selectedEvent, renderedEventItems.indexOf(selectedEvent))
      : "";

  return (
    <div className="observability-page" data-testid="observability-page">
      <div className="section-heading projection-page-heading">
        <div>
          <h2>Observability</h2>
          <span className="muted">trace workspace, runtime signals, logs, tokens, feedback, and raw diagnostics</span>
        </div>
        <span className={`metric-chip tone-${streamTone}`}>{liveState}</span>
      </div>
      <ObservabilityStateNotice
        hiddenNoiseCount={hiddenNoiseCount}
        liveState={liveState}
        snapshotReady={snapshotReady}
        truncatedEventCount={truncatedEventCount}
      />
      <div className="runtime-health-strip observability-strip" aria-label="Observability scope">
        <span><strong>Project</strong><em className="mono">{activeProjectId || snapshot?.project.project_id || "default"}</em></span>
        <span><strong>Stream</strong><em className={`badge badge-${streamTone}`}>{liveState}</em></span>
        <span><strong>Runtime</strong><em>{snapshot?.runtime.live ? "live" : "stopped"}</em></span>
        <span><strong>Truth</strong><em>EventLog / TaskStore</em></span>
        <span><strong>Raw</strong><em>redacted read-only</em></span>
      </div>
      <ProjectionMetricGrid metrics={metrics} />
      <div className="tab-row compact-tabs observability-tabs" aria-label="Observability tabs">
        {tabs.map((item) => (
          <button
            className={`tab-button ${tab === item.id ? "active" : ""}`}
            key={item.id}
            type="button"
            onClick={() => openTab(item.id)}
          >
            {item.label}{typeof item.count === "number" ? <span className="muted"> {item.count}</span> : null}
          </button>
        ))}
      </div>

      {tab === "traces" ? (
        <div className="observability-workbench">
          <section className="subsection observability-left-pane">
            <TraceFilterBar
              filters={traceFilters}
              rows={traceRows}
              onChange={setTraceFilters}
            />
            <TraceIndexList rows={filteredTraceRows} onOpen={openTrace} />
          </section>
          <section className="subsection observability-main-pane">
            <div className="inline-heading">
              <h3>Trace Detail</h3>
              <span className="muted">{traceDetailReady ? selectedTraceId : "select a trace"}</span>
            </div>
            {traceDetailReady && projectionDetail ? (
              <TraceDetailPanel detail={projectionDetail} />
            ) : (
              <ProjectionEmptyState
                state={{
                  title: snapshotReady ? "Select a trace" : "Trace snapshot pending",
                  description: snapshotReady
                    ? "Open a trace row to inspect the graph route, waterfall, source events, and task links."
                    : "Trace projections will appear after the project snapshot finishes loading.",
                  icon: MapIcon,
                  compact: true,
                }}
              />
            )}
          </section>
          <EventInspector event={selectedEvent} onOpenChannel={onOpenChannel} />
        </div>
      ) : null}

      {tab === "events" ? (
        <div className="observability-workbench events-workbench">
          <section className="subsection observability-left-pane event-control-pane">
            <div className="inline-heading">
              <h3>Event Views</h3>
              <span className="muted">{eventItems.length} loaded</span>
            </div>
            <div className="event-saved-view-list" aria-label="Event saved views">
              {eventSavedViews.map((view) => (
                <button
                  className={`event-saved-view ${view.active ? "active" : ""}`}
                  key={view.label}
                  type="button"
                  onClick={() => setEventFilter(view.filter)}
                >
                  <span>
                    <strong>{view.label}</strong>
                    <small>{view.description}</small>
                  </span>
                  <em>{view.count}</em>
                </button>
              ))}
            </div>
            <div className="event-filter-panel">
              <label>
                <span>Query</span>
                <input
                  className="filter-input observability-filter"
                  placeholder="task:TASK actor:dev-1 type:test.failed"
                  value={eventFilter}
                  onChange={(event) => setEventFilter(event.target.value)}
                />
              </label>
              {activeEventChips.length ? (
                <div className="event-filter-chips compact" aria-label="Active event filters">
                  {activeEventChips.map((chip) => (
                    <button
                      className="event-filter-chip mono"
                      key={`${chip.label}:${chip.value}`}
                      type="button"
                      onClick={() => {
                        if (chip.key === "query") setEventFilter("");
                        else setEventFilter(removeEventFilterToken(eventFilter, chip.key));
                      }}
                      aria-label={`Clear ${chip.label} filter`}
                    >
                      <span>{chip.label}:{chip.value}</span>
                      <X size={13} aria-hidden="true" />
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
            <div className="event-audit-mini-grid" aria-label="Audit projection summary">
              <div>
                <span>Mutation audit</span>
                <strong>{mutationAuditCount}</strong>
              </div>
              <div className={worktreeActionCount ? "needs-action" : ""}>
                <span>Worktree action</span>
                <strong>{worktreeActionCount}</strong>
              </div>
            </div>
          </section>
          <section className="subsection observability-main-pane">
            <div className="inline-heading">
              <h3>Event Stream</h3>
              <span className="muted">{renderedEventItems.length}/{eventItems.length} read-only</span>
            </div>
            <EventReplayControls
              autoFollow={autoFollowEvents}
              foldNoise={foldNoiseEvents}
              hiddenNoiseCount={hiddenNoiseCount}
              playing={replayPlaying}
              position={replayIndex}
              total={replayEvents.length}
              onAutoFollowChange={setAutoFollowEvents}
              onFoldNoiseChange={setFoldNoiseEvents}
              onJump={selectReplay}
              onPlayingChange={setReplayPlaying}
            />
            {renderedEventItems.length ? (
              <EventTable
                events={renderedEventItems}
                onOpenChannel={onOpenChannel}
                onSelect={(key) => {
                  const event = eventFromKey(renderedEventItems, key);
                  if (event) selectEventFromDisplay(event);
                }}
                selectedEventKey={renderedSelectedEventKey}
              />
            ) : (
              <ProjectionEmptyState
                state={{
                  title: snapshotReady ? "No events match this view" : "Event snapshot pending",
                  description: snapshotReady ? "Adjust filters or wait for runtime events." : "Events will appear after the project snapshot loads.",
                  icon: Radio,
                  compact: true,
                }}
              />
            )}
          </section>
          <EventInspector event={selectedEvent} onOpenChannel={onOpenChannel} />
        </div>
      ) : null}

      {tab === "runs" ? (
        <ProjectionList
          emptyState={{ title: "No workflow runs yet", description: "Runs appear after zf start, workflow invoke events, or archived runtime execution records.", icon: PlayCircle, compact: true }}
          title="Runs"
          rows={runs}
          idKey="run_id"
          onOpen={(id) => onOpenProjection("run", id)}
          detail={textValue(projectionDetail?.run_id) ? projectionDetail : null}
        />
      ) : null}
      {tab === "fanouts" ? (
        <ProjectionList
          emptyState={{ title: "No fanout executions yet", description: "Fanout lanes appear after task_map.ready, STAR fanout dispatch, or workflow fan-out events.", icon: GitFork, compact: true }}
          title="Fanouts"
          rows={fanouts}
          idKey="fanout_id"
          onOpen={(id) => onOpenProjection("fanout", id)}
          detail={textValue(projectionDetail?.fanout_id) ? projectionDetail : null}
        />
      ) : null}
      {tab === "candidates" ? (
        <ProjectionList
          emptyState={{ title: "No candidates yet", description: "Candidate projections appear after writer fanout, integration, or ship-candidate events are recorded.", icon: Boxes, compact: true }}
          title="Candidates"
          rows={candidates}
          idKey="pdd_id"
          onOpen={(id) => onOpenProjection("candidate", id)}
          detail={textValue(projectionDetail?.pdd_id) ? projectionDetail : null}
        />
      ) : null}
      {tab === "integration" ? (
        <IntegrationQueuePanel queue={integrationQueue} />
      ) : null}
      {tab === "repair" ? (
        <RepairActionsPanel projection={repairActions} />
      ) : null}
      {tab === "logs" ? (
        <LogsPanel projectId={activeProjectId || snapshot?.project.project_id} />
      ) : null}
      {tab === "raw" ? (
        <div className="observability-resource-grid">
          <KeyValuePanel
            title="Raw Boundary"
            rows={[
              { key: "mode", value: "read-only projection" },
              { key: "redaction", value: "client display redaction + server redaction" },
              { key: "project_scope", value: activeProjectId || snapshot?.project.project_id || "-" },
              { key: "event_count", value: eventItems.length },
            ]}
          />
          <section className="subsection">
            <div className="inline-heading">
              <h3>Selected Raw</h3>
              <span className="muted">redacted</span>
            </div>
            <PreBlock value={selectedEvent || projectionDetail || snapshot?.runtime || {}} />
          </section>
        </div>
      ) : null}
    </div>
  );
}


function IntegrationQueuePanel({ queue }: { queue: IntegrationQueueProjection | null }) {
  const summary = queue?.summary;
  const arbiter = queue?.arbiter;
  const entries = queue?.entries ?? [];
  const staleEntries = queue?.stale_entries ?? [];
  const issues = queue?.issues ?? [];
  const queueRows = entries.map((entry) => ({
    entry_id: entry.id,
    status: entry.status,
    task_id: entry.task_id || "-",
    fanout: entry.fanout_instance_id || "-",
    retry_count: entry.retry_count ?? 0,
    evidence: integrationQueueEvidenceCount(entry),
    source_ref: entry.source_ref || "-",
    reason: entry.reason || "-",
    updated_at: entry.updated_at || "-",
  }));
  const staleRows = staleEntries.map((entry) => ({
    entry_id: entry.id,
    task_id: entry.task_id || "-",
    fanout: entry.fanout_instance_id || "-",
    stale_reason: textValue(entry.stale_reason) || entry.reason || "-",
    superseded_by: textValue(entry.superseded_by) || "-",
    source_ref: entry.source_ref || "-",
  }));
  const issueRows = issues.map((issue) => ({
    entry_id: textValue(issue.entry_id) || "-",
    event_type: textValue(issue.event_type) || "-",
    reason: textValue(issue.reason) || "-",
    event_id: textValue(issue.event_id) || "-",
  }));
  const arbiterRows = (arbiter?.decisions ?? []).map((decision) => {
    const dirtyGuard = asRecord(decision.dirty_guard);
    const mergeSafety = asRecord(decision.merge_safety);
    const controlledAction = asRecord(decision.controlled_action);
    return {
      decision_id: decision.id,
      status: decision.status,
      decision: decision.decision,
      queue_entry: decision.queue_entry_id,
      queue_status: decision.queue_status,
      target_event: decision.target_event_type || "-",
      dirty: textValue(dirtyGuard.dirty) || "false",
      preflight: textValue(mergeSafety.merge_preflight_passed) || "-",
      controlled_action: textValue(controlledAction.action) || textValue(controlledAction.surface) || "-",
      idempotency_key: decision.idempotency_key,
      reason: decision.reason || "-",
    };
  });
  const needsReview = entries.filter((entry) => entry.status === "needs_review");
  const counts = [
    ["queued", summary?.queued ?? 0],
    ["integrating", summary?.integrating ?? 0],
    ["needs_review", summary?.needs_review ?? 0],
    ["integrated", summary?.integrated ?? 0],
    ["discarded", summary?.discarded ?? 0],
    ["stale", summary?.stale_rejected ?? 0],
    ["issues", summary?.issue_count ?? 0],
    ["arbiter", Number(arbiter?.summary?.total ?? 0)],
  ];

  return (
    <div className="integration-queue-panel">
      <div className="runtime-health-strip integration-queue-strip" aria-label="Integration queue summary">
        <span><strong>Schema</strong><em className="mono">{queue?.schema_version ?? "-"}</em></span>
        {counts.map(([label, value]) => (
          <span key={label}>
            <strong>{label}</strong>
            <em>{value}</em>
          </span>
        ))}
      </div>

      {needsReview.length ? (
        <section className="subsection integration-review-strip">
          <div className="inline-heading">
            <h3>Needs Review</h3>
            <span className="muted">{needsReview.length} entries</span>
          </div>
          <div className="compact-list">
            {needsReview.slice(0, 6).map((entry) => (
              <div className="inline-row integration-queue-review-row" key={entry.id}>
                <span className="status-pill status-rejected">{entry.status}</span>
                <span className="mono">{entry.id}</span>
                <span>{entry.task_id || "-"}</span>
                <span className="muted">{entry.reason || "-"}</span>
                <span className="muted mono">{entry.handoff_ref || entry.source_ref || "-"}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <div className="observability-resource-grid integration-queue-grid">
        <TablePage
          title="Integration Queue"
          rows={queueRows}
          embedded
          emptyState={{
            title: queue ? "No integration entries" : "Integration queue pending",
            description: queue ? "No queued, integrating, or review entries are projected." : "Queue projection will load after the project API responds.",
            icon: GitFork,
            compact: true,
          }}
        />
        <TablePage
          title="Integration Arbiter"
          rows={arbiterRows}
          embedded
          emptyState={{
            title: "No arbiter decisions",
            description: "Decision-only integration intents appear after queue entries are projected.",
            icon: GitFork,
            compact: true,
          }}
        />
        <TablePage
          title="Stale Queue Events"
          rows={staleRows}
          embedded
          emptyState={{
            title: "No stale integration entries",
            description: "Superseded fanout entries and explicitly stale queue events appear here.",
            icon: Archive,
            compact: true,
          }}
        />
      </div>

      {issueRows.length ? (
        <TablePage
          title="Integration Queue Issues"
          rows={issueRows}
          embedded
          emptyState={{
            title: "No integration queue issues",
            description: "Illegal transitions and malformed queue events appear here.",
            icon: Bell,
            compact: true,
          }}
        />
      ) : null}
    </div>
  );
}


function integrationQueueEvidenceCount(entry: IntegrationQueueEntry): number {
  return (entry.artifact_refs?.length ?? 0) + (entry.verification_refs?.length ?? 0);
}


function RepairActionsPanel({ projection }: { projection: RepairActionProjection | null }) {
  const summary = projection?.summary;
  const actions = projection?.actions ?? [];
  const issues = projection?.issues ?? [];
  const rows = actions.map((action) => ({
    action_id: action.id,
    status: action.status,
    kind: action.kind,
    task_id: action.task_id || "-",
    target: repairActionTarget(action),
    evidence: action.evidence_refs?.length ?? 0,
    reason: action.reason || "-",
    updated_at: action.updated_at || "-",
  }));
  const attentionActions = actions.filter((action) => (
    ["pending", "rejected", "invalid"].includes(action.status)
  ));
  const issueRows = issues.map((issue) => ({
    action_id: textValue(issue.action_id) || "-",
    event_type: textValue(issue.event_type) || "-",
    reason: textValue(issue.reason) || "-",
    event_id: textValue(issue.event_id) || "-",
  }));
  const counts = [
    ["pending", summary?.pending ?? 0],
    ["applied", summary?.applied ?? 0],
    ["rejected", summary?.rejected ?? 0],
    ["invalid", summary?.invalid ?? 0],
    ["duplicate", summary?.duplicate ?? 0],
    ["issues", summary?.issue_count ?? 0],
  ];

  return (
    <div className="repair-actions-panel">
      <div className="runtime-health-strip repair-actions-strip" aria-label="Repair action summary">
        <span><strong>Schema</strong><em className="mono">{projection?.schema_version ?? "-"}</em></span>
        {counts.map(([label, value]) => (
          <span key={label}>
            <strong>{label}</strong>
            <em>{value}</em>
          </span>
        ))}
      </div>

      {attentionActions.length ? (
        <section className="subsection repair-attention-strip">
          <div className="inline-heading">
            <h3>Action Attention</h3>
            <span className="muted">{attentionActions.length} actions</span>
          </div>
          <div className="compact-list">
            {attentionActions.slice(0, 8).map((action) => (
              <div className="inline-row repair-action-attention-row" key={action.id}>
                <span className={`status-pill ${repairActionStatusClass(action.status)}`}>{action.status}</span>
                <span className="mono">{action.id}</span>
                <span>{action.kind || "-"}</span>
                <span className="muted">{repairActionTarget(action)}</span>
                <span className="muted">{action.reason || "-"}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <TablePage
        title="Repair Actions"
        rows={rows}
        embedded
        emptyState={{
          title: projection ? "No repair actions" : "Repair actions pending",
          description: projection ? "No structured repair action has been requested." : "Repair action projection will load after the project API responds.",
          icon: Wrench,
          compact: true,
        }}
      />

      {issueRows.length ? (
        <TablePage
          title="Repair Action Issues"
          rows={issueRows}
          embedded
          emptyState={{
            title: "No repair action issues",
            description: "Invalid requests and duplicate idempotency keys appear here.",
            icon: Bell,
            compact: true,
          }}
        />
      ) : null}
    </div>
  );
}


function repairActionTarget(action: RepairActionRecord): string {
  return action.worker_id
    || action.role
    || action.fanout_child_id
    || action.queue_entry_id
    || action.projection
    || action.fanout_id
    || action.task_id
    || "-";
}


function repairActionStatusClass(status: string): string {
  if (status === "applied") return "status-completed";
  if (status === "pending" || status === "duplicate") return "status-pending";
  return "status-rejected";
}




function TraceFilterBar({
  filters,
  onChange,
  rows,
}: {
  filters: TraceFilters;
  onChange: (filters: TraceFilters) => void;
  rows: TraceSummary[];
}) {
  const roles = uniqueSorted(rows.flatMap((row) => row.actors ?? [])).slice(0, 12);
  const backends = uniqueSorted(rows.flatMap((row) => row.backends ?? [])).slice(0, 12);
  return (
    <div className="observability-filter-panel" aria-label="Trace filters">
      <input
        className="filter-input"
        placeholder="trace, task, actor, type"
        value={filters.query}
        onChange={(event) => onChange({ ...filters, query: event.target.value })}
      />
      <div className="observability-filter-grid">
        <label>
          <span>Status</span>
          <select value={filters.status} onChange={(event) => onChange({ ...filters, status: event.target.value as TraceStatusFilter })}>
            <option value="all">All</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="blocked">Blocked</option>
            <option value="observed">Observed</option>
          </select>
        </label>
        <label>
          <span>Duration</span>
          <select value={filters.duration} onChange={(event) => onChange({ ...filters, duration: event.target.value as TraceDurationFilter })}>
            <option value="all">All</option>
            <option value="short">&lt; 1m</option>
            <option value="medium">1m - 10m</option>
            <option value="long">&gt; 10m</option>
            <option value="unknown">Unknown</option>
          </select>
        </label>
        <label>
          <span>Role</span>
          <input
            list="observability-role-options"
            value={filters.role}
            onChange={(event) => onChange({ ...filters, role: event.target.value })}
            placeholder="dev"
          />
          <datalist id="observability-role-options">
            {roles.map((role) => <option key={role} value={role} />)}
          </datalist>
        </label>
        <label>
          <span>Backend</span>
          <input
            list="observability-backend-options"
            value={filters.backend}
            onChange={(event) => onChange({ ...filters, backend: event.target.value })}
            placeholder="codex"
          />
          <datalist id="observability-backend-options">
            {backends.map((backend) => <option key={backend} value={backend} />)}
          </datalist>
        </label>
      </div>
      <div className="observability-filter-actions">
        <span className="muted">{rows.length} source traces</span>
        <button
          className="icon-button"
          type="button"
          onClick={() => onChange(defaultTraceFilters())}
        >
          Reset
        </button>
      </div>
    </div>
  );
}


function EventReplayControls({
  autoFollow,
  foldNoise,
  hiddenNoiseCount,
  onAutoFollowChange,
  onFoldNoiseChange,
  onJump,
  onPlayingChange,
  playing,
  position,
  total,
}: {
  autoFollow: boolean;
  foldNoise: boolean;
  hiddenNoiseCount: number;
  onAutoFollowChange: (value: boolean) => void;
  onFoldNoiseChange: (value: boolean) => void;
  onJump: (index: number) => void;
  onPlayingChange: (value: boolean) => void;
  playing: boolean;
  position: number;
  total: number;
}) {
  const disabled = total === 0;
  return (
    <div className="observability-replay-bar" aria-label="Event replay controls">
      <div className="observability-replay-buttons">
        <button className="icon-button" disabled={disabled} type="button" onClick={() => onJump(0)} title="First event">
          <SkipBack size={14} aria-hidden="true" />
        </button>
        <button className="icon-button" disabled={disabled} type="button" onClick={() => onJump(position - 1)} title="Previous event">
          <ChevronRight className="flip-x" size={14} aria-hidden="true" />
        </button>
        <button className="icon-button primary" disabled={disabled} type="button" onClick={() => onPlayingChange(!playing)} title={playing ? "Pause replay" : "Play replay"}>
          {playing ? <PauseCircle size={14} aria-hidden="true" /> : <PlayCircle size={14} aria-hidden="true" />}
          {playing ? "Pause" : "Play"}
        </button>
        <button className="icon-button" disabled={disabled} type="button" onClick={() => onJump(position + 1)} title="Next event">
          <ChevronRight size={14} aria-hidden="true" />
        </button>
        <button className="icon-button" disabled={disabled} type="button" onClick={() => onJump(total - 1)} title="Latest event">
          <SkipForward size={14} aria-hidden="true" />
        </button>
      </div>
      <span className="muted mono">{disabled ? "0/0" : `${Math.min(position + 1, total)}/${total}`}</span>
      <label className="observability-toggle">
        <input type="checkbox" checked={autoFollow} onChange={(event) => onAutoFollowChange(event.target.checked)} />
        <span>Auto-follow</span>
      </label>
      <label className="observability-toggle">
        <input type="checkbox" checked={foldNoise} onChange={(event) => onFoldNoiseChange(event.target.checked)} />
        <span>Fold heartbeat</span>
      </label>
      {hiddenNoiseCount ? <span className="badge badge-muted">{hiddenNoiseCount} folded</span> : null}
    </div>
  );
}


function ObservabilityStateNotice({
  hiddenNoiseCount,
  liveState,
  snapshotReady,
  truncatedEventCount,
}: {
  hiddenNoiseCount: number;
  liveState: LiveState;
  snapshotReady: boolean;
  truncatedEventCount: number;
}) {
  const messages = [];
  if (!snapshotReady) messages.push("snapshot pending");
  if (liveState === "degraded") messages.push("stream degraded; latest snapshot is still read-only");
  if (liveState === "reconnecting") messages.push("reconnecting with last cursor");
  if (hiddenNoiseCount) messages.push(`${hiddenNoiseCount} heartbeat/progress events folded`);
  if (truncatedEventCount) messages.push(`${truncatedEventCount} older rows hidden for performance`);
  if (!messages.length) return null;
  return (
    <div className={`observability-state-notice ${liveState === "degraded" ? "is-degraded" : ""}`}>
      {messages.map((message) => <span key={message}>{message}</span>)}
    </div>
  );
}


function eventFromKey(events: EventRecord[], key: string): EventRecord | null {
  return events.find((event, index) => eventKey(event, index) === key) ?? null;
}


function defaultTraceFilters(): TraceFilters {
  return {
    query: "",
    status: "all",
    role: "",
    backend: "",
    duration: "all",
  };
}


function readInitialTraceFilters(): TraceFilters {
  if (typeof window === "undefined") return defaultTraceFilters();
  const params = new URLSearchParams(window.location.search);
  const status = params.get("obs_status") as TraceStatusFilter | null;
  const duration = params.get("obs_duration") as TraceDurationFilter | null;
  return {
    query: params.get("obs_q") ?? "",
    status: status && ["all", "running", "completed", "failed", "blocked", "observed"].includes(status) ? status : "all",
    role: params.get("obs_role") ?? "",
    backend: params.get("obs_backend") ?? "",
    duration: duration && ["all", "short", "medium", "long", "unknown"].includes(duration) ? duration : "all",
  };
}

function readTraceExplorerDeepLink(): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get("trace_id") ?? "";
}

function writeTraceExplorerDeepLink(traceId: string) {
  const params = new URLSearchParams(window.location.search);
  params.set("page", "traces");
  params.set("trace_id", traceId);
  window.history.replaceState(null, "", `?${params.toString()}`);
}


function readInitialObservabilityTab(activePage: PageId): ObservabilityTab {
  if (typeof window === "undefined" || activePage !== "observability") {
    return observabilityTabForPage(activePage);
  }
  const tab = new URLSearchParams(window.location.search).get("obs_tab");
  return isObservabilityTab(tab) ? tab : observabilityTabForPage(activePage);
}


function isObservabilityTab(value: string | null): value is ObservabilityTab {
  return Boolean(value && [
    "traces",
    "events",
    "logs",
    "runs",
    "fanouts",
    "candidates",
    "integration",
    "repair",
    "raw",
  ].includes(value));
}


function readInitialBooleanQuery(key: string, fallback: boolean): boolean {
  if (typeof window === "undefined") return fallback;
  const value = new URLSearchParams(window.location.search).get(key);
  if (value === "1" || value === "true") return true;
  if (value === "0" || value === "false") return false;
  return fallback;
}


function persistObservabilityQuery(
  tab: ObservabilityTab,
  filters: TraceFilters,
  options: { autoFollow: boolean; foldNoise: boolean },
) {
  if (typeof window === "undefined") return;
  const params = new URLSearchParams(window.location.search);
  params.set("obs_tab", tab);
  setOptionalQueryParam(params, "obs_q", filters.query);
  setOptionalQueryParam(params, "obs_role", filters.role);
  setOptionalQueryParam(params, "obs_backend", filters.backend);
  if (filters.status === "all") params.delete("obs_status");
  else params.set("obs_status", filters.status);
  if (filters.duration === "all") params.delete("obs_duration");
  else params.set("obs_duration", filters.duration);
  if (options.autoFollow) params.delete("obs_auto_follow");
  else params.set("obs_auto_follow", "0");
  if (options.foldNoise) params.delete("obs_fold_noise");
  else params.set("obs_fold_noise", "0");
  window.history.replaceState(null, "", `?${params.toString()}`);
}


function setOptionalQueryParam(params: URLSearchParams, key: string, value: string) {
  if (value.trim()) params.set(key, value.trim());
  else params.delete(key);
}


function traceMatchesFilters(row: TraceSummary, events: EventRecord[], filters: TraceFilters): boolean {
  const status = traceStatus(row);
  if (filters.status !== "all" && status !== filters.status) return false;
  const duration = traceDurationBucket(row);
  if (filters.duration !== "all" && duration !== filters.duration) return false;
  const signals = traceSignals(row, events);
  if (filters.role.trim() && !signals.roles.some((item) => item.includes(filters.role.trim().toLowerCase()))) return false;
  if (filters.backend.trim() && !signals.backends.some((item) => item.includes(filters.backend.trim().toLowerCase()))) return false;
  const query = filters.query.trim().toLowerCase();
  if (!query) return true;
  return signals.text.includes(query);
}


function traceStatus(row: TraceSummary): TraceStatusFilter {
  const explicit = String(row.status || "").toLowerCase();
  const eventType = String(row.last_type || "").toLowerCase();
  const source = `${explicit} ${eventType}`;
  if (source.includes("blocked")) return "blocked";
  if (/(failed|error|rejected)/.test(source)) return "failed";
  if (/(completed|done|passed|approved|accepted)/.test(source)) return "completed";
  if (/(running|started|progress|in_progress|dispatched)/.test(source)) return "running";
  return "observed";
}


function traceDurationBucket(row: TraceSummary): TraceDurationFilter {
  const seconds = typeof row.duration_seconds === "number" ? row.duration_seconds : traceDurationSeconds(row);
  if (seconds == null) return "unknown";
  if (seconds < 60) return "short";
  if (seconds < 600) return "medium";
  return "long";
}


function traceDurationSeconds(row: TraceSummary): number | null {
  const first = Date.parse(row.first_ts || "");
  const last = Date.parse(row.last_ts || "");
  if (!Number.isFinite(first) || !Number.isFinite(last)) return null;
  return Math.max(0, Math.round((last - first) / 1000));
}


function traceSignals(row: TraceSummary, events: EventRecord[]) {
  const traceId = String(row.trace_id || "");
  const linked = events.filter((event) => eventBelongsToTrace(event, row));
  const roles = uniqueSorted([
    ...(row.actors ?? []),
    ...linked.map((event) => event.actor || ""),
    ...linked.map((event) => textValue(asRecord(event.payload).role)),
    ...linked.map((event) => textValue(asRecord(event.payload).role_id)),
  ]).map((item) => item.toLowerCase());
  const backends = uniqueSorted([
    ...(row.backends ?? []),
    ...linked.flatMap(eventBackendSignals),
    ...backendLikeSignals(row.actors ?? []),
  ]).map((item) => item.toLowerCase());
  const text = [
    traceId,
    row.last_type,
    ...(row.task_ids ?? []),
    ...(row.actors ?? []),
    ...(row.backends ?? []),
    ...linked.map((event) => event.type),
    ...linked.map((event) => eventSummary(event)),
  ].join(" ").toLowerCase();
  return { backends, roles, text };
}


function eventBelongsToTrace(event: EventRecord, row: TraceSummary): boolean {
  const payload = asRecord(event.payload);
  const traceId = String(row.trace_id || "");
  if (!traceId) return false;
  if (textValue(payload.trace_id) === traceId) return true;
  if (String(event.correlation_id || "") === traceId) return true;
  if (String(payload.run_id || payload.fanout_id || "") === traceId) return true;
  if (event.task_id && row.task_ids?.includes(event.task_id)) return true;
  return false;
}


function eventBackendSignals(event: EventRecord): string[] {
  const payload = asRecord(event.payload);
  return uniqueSorted([
    textValue(payload.backend),
    textValue(payload.provider),
    textValue(payload.model),
    textValue(payload.transport),
    ...backendLikeSignals([event.actor || ""]),
  ]);
}


function backendLikeSignals(values: string[]): string[] {
  return values.filter((value) => /codex|claude|openclaw|headless|cli/i.test(value));
}


function uniqueSorted(values: Array<string | null | undefined>): string[] {
  return sortedStrings(values.map((value) => String(value || "").trim()).filter(Boolean));
}


function sortedStrings(values: string[]): string[] {
  return Array.from(new Set(values)).sort((left, right) => left.localeCompare(right));
}


function EventInspector({
  event,
  onOpenChannel,
}: {
  event: EventRecord | null;
  onOpenChannel: (channelId: string) => void;
}) {
  const channelId = eventChannelId(event);
  const payloadRows = eventPayloadHighlights(event);
  return (
    <aside className="subsection event-inspector" aria-label="Event inspector">
      <div className="inline-heading">
        <h3>JSON Inspector</h3>
        {channelId ? (
          <button className="icon-button" type="button" onClick={() => onOpenChannel(channelId)}>
            Open Channel
          </button>
        ) : null}
      </div>
      {event ? (
        <>
          <div className={`event-inspector-summary event-family-${eventFamily(event.type)}`}>
            <span className="event-type-pill mono">{event.type}</span>
            <p>{eventSummary(event)}</p>
          </div>
          <dl className="key-value-grid compact-kv">
            <dt>seq</dt>
            <dd>{event.seq ?? "-"}</dd>
            <dt>type</dt>
            <dd className="mono">{event.type}</dd>
            <dt>actor</dt>
            <dd>{event.actor || "-"}</dd>
            <dt>task</dt>
            <dd className="mono">{event.task_id || "-"}</dd>
            <dt>channel</dt>
            <dd className="mono">{channelId || "-"}</dd>
            <dt>ts</dt>
            <dd className="mono">{event.ts || "-"}</dd>
          </dl>
          {payloadRows.length ? (
            <div className="event-payload-section">
              <h4>Payload</h4>
              <dl className="key-value-grid compact-kv">
                {payloadRows.map(([key, value]) => (
                  <Fragment key={key}>
                    <dt>{key}</dt>
                    <dd className="mono">{value}</dd>
                  </Fragment>
                ))}
              </dl>
            </div>
          ) : null}
          <details className="event-raw-json">
            <summary>Raw JSON</summary>
            <PreBlock value={event} />
          </details>
        </>
      ) : (
        <p className="empty-text">No event selected.</p>
      )}
    </aside>
  );
}
