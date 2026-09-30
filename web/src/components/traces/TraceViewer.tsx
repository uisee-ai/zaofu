import { useSettingsPreferences } from "../../settings/useSettingsPreferences";
import { uiText } from "../../settings/uiText";
import { ArrowLeft } from "lucide-react";

import type {
  TraceDetail,
  TraceEventDetail,
  TraceSpanPage,
  TraceTimelineEvent,
} from "../../api/types";
import {
  TraceInspector,
  type TraceInspectorNode,
  type TraceInspectorTab,
} from "./TraceInspector";
import { TraceSpanSurface, type TraceSpanView } from "./TraceSpanViews";
import { formatDuration, formatTimestamp, traceStatus } from "./traceModel";
import type { ResolvedTraceViewerMode } from "./traceSpanModel";
import "../../styles/26-trace-viewer.css";

export interface TraceViewerSelection {
  eventId: string;
  spanId: string;
  stageId: string;
}

export interface TraceViewerResources {
  detail: { data: TraceDetail | null; error: string; loading: boolean };
  spans: { data: TraceSpanPage | null; error: string; loading: boolean };
  raw: { data: TraceEventDetail | null; error: string; loading: boolean };
}

export interface TraceViewerActions {
  close: () => void;
  loadEarlierEvents: () => void;
  loadEarlierSpans: () => void;
  selectEvent: (eventId: string) => void;
  selectInspectorTab: (tab: TraceInspectorTab) => void;
  selectMode: (mode: ResolvedTraceViewerMode) => void;
  selectSpan: (spanId: string) => void;
  selectSpanView: (view: TraceSpanView) => void;
  selectStage: (stageId: string) => void;
}

export function TraceViewer({
  actions,
  earlierError,
  earlierLoading,
  earlierSpansError,
  earlierSpansLoading,
  inspectorTab,
  mode,
  rawEventId,
  resources,
  selection,
  spanView,
  traceId,
}: {
  actions: TraceViewerActions;
  earlierError: string;
  earlierLoading: boolean;
  earlierSpansError: string;
  earlierSpansLoading: boolean;
  inspectorTab: TraceInspectorTab;
  mode: ResolvedTraceViewerMode;
  rawEventId: string;
  resources: TraceViewerResources;
  selection: TraceViewerSelection;
  spanView: TraceSpanView;
  traceId: string;
}) {
  useSettingsPreferences();
  const detail = resources.detail.data;
  const route = detail?.execution_route;
  const displayStatus = route && !route.empty ? route.status : detail?.status ?? "observed";
  const status = traceStatus({ status: displayStatus, last_type: "" });
  const spanCount = resources.spans.data?.span_count ?? resources.spans.data?.items.length ?? 0;

  return (
    <section aria-label={`Trace ${traceId}`} className="trace-viewer">
      <header className="trace-viewer-heading">
        <button aria-label="Back to traces" className="icon-button trace-viewer-back" type="button" onClick={actions.close}>
          <ArrowLeft aria-hidden="true" size={17} />
          <span>{uiText("Traces")}</span>
        </button>
        <div className="trace-viewer-title">
          <span className="muted">{uiText("Trace")}</span>
          <h3 className="mono">{traceId}</h3>
        </div>
        <div className="trace-viewer-summary" aria-label="Trace summary">
          <span className={`trace-status trace-status-${status}`}>{displayStatus}</span>
          <span>{formatDuration(detail?.duration_seconds)}</span>
          <span>{detail ? `${detail.event_count} events` : "Loading events…"}</span>
          {detail?.tasks.length ? <span>{detail.tasks.length}{detail.tasks_truncated ? "+" : ""} tasks</span> : null}
        </div>
      </header>

      <nav aria-label="Trace views" className="trace-viewer-tabs" role="tablist">
        <ViewerTab active={mode === "spans"} label={`Spans${resources.spans.loading ? " · …" : ` · ${spanCount}`}`} mode="spans" onSelect={actions.selectMode} />
        <ViewerTab active={mode === "execution"} label={`Execution${route?.linear.length ? ` · ${route.linear.length}` : ""}`} mode="execution" onSelect={actions.selectMode} />
        <ViewerTab active={mode === "events"} label={`Events${detail ? ` · ${detail.event_count}` : ""}`} mode="events" onSelect={actions.selectMode} />
      </nav>

      <div className="trace-viewer-body">
        {mode === "spans" ? (
          <SpanWorkspace
            actions={actions}
            earlierError={earlierSpansError}
            earlierLoading={earlierSpansLoading}
            inspectorTab={inspectorTab}
            rawEventId={rawEventId}
            resources={resources}
            selectedSpanId={selection.spanId}
            spanView={spanView}
          />
        ) : null}
        {mode === "execution" ? (
          <ExecutionWorkspace
            actions={actions}
            inspectorTab={inspectorTab}
            rawEventId={rawEventId}
            resources={resources}
            selectedStageId={selection.stageId}
          />
        ) : null}
        {mode === "events" ? (
          <EventsWorkspace
            actions={actions}
            earlierError={earlierError}
            earlierLoading={earlierLoading}
            inspectorTab={inspectorTab}
            rawEventId={rawEventId}
            resources={resources}
            selectedEventId={selection.eventId}
          />
        ) : null}
      </div>
    </section>
  );
}

function ViewerTab({
  active,
  label,
  mode,
  onSelect,
}: {
  active: boolean;
  label: string;
  mode: ResolvedTraceViewerMode;
  onSelect: (mode: ResolvedTraceViewerMode) => void;
}) {
  return (
    <button
      aria-controls={`trace-${mode}-panel`}
      aria-selected={active}
      role="tab"
      type="button"
      onClick={() => onSelect(mode)}
    >
      {label}
    </button>
  );
}

function SpanWorkspace({
  actions,
  earlierError,
  earlierLoading,
  inspectorTab,
  rawEventId,
  resources,
  selectedSpanId,
  spanView,
}: {
  actions: TraceViewerActions;
  earlierError: string;
  earlierLoading: boolean;
  inspectorTab: TraceInspectorTab;
  rawEventId: string;
  resources: TraceViewerResources;
  selectedSpanId: string;
  spanView: TraceSpanView;
}) {
  useSettingsPreferences();
  const page = resources.spans.data;
  const selectedSpan = page?.items.find((span) => span.span_id === selectedSpanId) ?? null;
  const node: TraceInspectorNode = selectedSpan ? { kind: "span", value: selectedSpan } : null;
  return (
    <div className="trace-workspace" id="trace-spans-panel" role="tabpanel">
      <main className="trace-workspace-primary">
        <div className="trace-surface-toolbar">
          <div>
            <strong>Lifecycle spans</strong>
            <span className="muted">{uiText("Only stable, ledger-backed lifecycle boundaries are shown.")}</span>
          </div>
          <div aria-label="Span layout" className="trace-view-toggle" role="tablist">
            {(["tree", "waterfall"] as const).map((view) => (
              <button
                aria-selected={spanView === view}
                key={view}
                role="tab"
                type="button"
                onClick={() => actions.selectSpanView(view)}
              >
                {view === "tree" ? "Tree" : "Waterfall"}
              </button>
            ))}
          </div>
          {page?.has_more ? (
            <button className="icon-button" disabled={earlierLoading} type="button" onClick={actions.loadEarlierSpans}>
              {earlierLoading ? "Loading…" : "Load earlier spans"}
            </button>
          ) : null}
        </div>
        <div className="trace-surface-scroll" aria-busy={resources.spans.loading}>
          {resources.spans.loading ? <TraceState>{uiText("Loading bounded lifecycle spans…")}</TraceState> : null}
          {resources.spans.error ? <p className="trace-load-error" role="alert">Span coverage unavailable: {resources.spans.error}</p> : null}
          {earlierError ? <p className="trace-load-error" role="alert">Earlier spans unavailable: {earlierError}</p> : null}
          {!resources.spans.loading && !resources.spans.error && page?.items.length ? (
            <>
              <TraceSpanSurface
                onSelect={actions.selectSpan}
                selectedSpanId={selectedSpanId}
                spans={page.items}
                view={spanView}
              />
              {page.has_more ? <p className="trace-bounded-note">Showing {page.items.length} of {page.span_count} bounded lifecycle spans.</p> : null}
            </>
          ) : null}
          {!resources.spans.loading && !resources.spans.error && page && !page.items.length ? (
            <TraceSpanCoverageEmpty actions={actions} page={page} />
          ) : null}
        </div>
      </main>
      <TraceInspector
        node={node}
        rawEventId={rawEventId}
        rawState={resources.raw}
        tab={inspectorTab}
        onTabChange={actions.selectInspectorTab}
      />
    </div>
  );
}

function TraceSpanCoverageEmpty({ actions, page }: { actions: TraceViewerActions; page: TraceSpanPage }) {
  useSettingsPreferences();
  const coverage = page.coverage;
  return (
    <div className="trace-coverage-empty">
      <strong>{uiText("No lifecycle spans are proven for this trace.")}</strong>
      <p>{coverage.reason || "The bounded ledger window contains no paired lifecycle boundaries."}</p>
      <dl>
        <div><dt>{uiText("Collector")}</dt><dd>{coverage.collector || "not observed"}</dd></div>
        <div><dt>{uiText("Ledger")}</dt><dd>{coverage.ledger || "events.jsonl"}</dd></div>
        <div><dt>{uiText("Coverage")}</dt><dd>{coverage.status}</dd></div>
      </dl>
      <div className="trace-coverage-actions">
        <button className="icon-button" type="button" onClick={() => actions.selectMode("execution")}>{uiText("Open Execution")}</button>
        <button className="icon-button" type="button" onClick={() => actions.selectMode("events")}>{uiText("Open Events")}</button>
      </div>
    </div>
  );
}

function ExecutionWorkspace({
  actions,
  inspectorTab,
  rawEventId,
  resources,
  selectedStageId,
}: {
  actions: TraceViewerActions;
  inspectorTab: TraceInspectorTab;
  rawEventId: string;
  resources: TraceViewerResources;
  selectedStageId: string;
}) {
  useSettingsPreferences();
  const detail = resources.detail.data;
  const route = detail?.execution_route;
  const selectedStage = route?.linear.find((stage) => stageKey(stage.stage, stage.first_seq) === selectedStageId) ?? null;
  const node: TraceInspectorNode = selectedStage ? { kind: "stage", value: selectedStage } : null;
  return (
    <div className="trace-workspace" id="trace-execution-panel" role="tabpanel">
      <main className="trace-workspace-primary trace-execution-surface" aria-busy={resources.detail.loading}>
        <div className="trace-surface-toolbar">
          <div><strong>{uiText("Execution route")}</strong><span className="muted">ZaoFu stages remain distinct from lifecycle spans.</span></div>
        </div>
        <div className="trace-surface-scroll">
          {resources.detail.loading ? <TraceState>{uiText("Loading bounded execution evidence…")}</TraceState> : null}
          {resources.detail.error ? <p className="trace-load-error" role="alert">Trace detail unavailable: {resources.detail.error}</p> : null}
          {!resources.detail.loading && !resources.detail.error && (!route || route.empty) ? (
            <TraceState>{uiText("No structured route is available for this trace.")}</TraceState>
          ) : null}
          {route && !route.empty ? (
            <>
              <div className="trace-route-summary">
                <span className={`trace-status trace-status-${traceStatus({ status: route.status, last_type: "" })}`}>{route.status}</span>
                <strong>{route.summary || route.current_stage_label || route.current_stage}</strong>
              </div>
              <ol className="trace-route-list">
                {route.linear.map((stage) => {
                  const id = stageKey(stage.stage, stage.first_seq);
                  return (
                    <li key={id}>
                      <button
                        aria-current={selectedStageId === id ? "true" : undefined}
                        className={selectedStageId === id ? "selected" : ""}
                        type="button"
                        onClick={() => actions.selectStage(id)}
                      >
                        <span className="trace-route-marker" aria-hidden="true" />
                        <span className="trace-route-copy">
                          <strong>{stage.label || stage.stage}</strong>
                          <span className="muted">
                            {stage.actors.join(", ") || "system"} · {stage.event_count} events
                            {stage.failed_count ? ` · ${stage.failed_count} failed` : ""}
                          </span>
                        </span>
                        <span className={`trace-status trace-status-${traceStatus({ status: stage.status, last_type: "" })}`}>{stage.status}</span>
                      </button>
                    </li>
                  );
                })}
              </ol>
            </>
          ) : null}
        </div>
      </main>
      <TraceInspector node={node} rawEventId={rawEventId} rawState={resources.raw} tab={inspectorTab} onTabChange={actions.selectInspectorTab} />
    </div>
  );
}

function EventsWorkspace({
  actions,
  earlierError,
  earlierLoading,
  inspectorTab,
  rawEventId,
  resources,
  selectedEventId,
}: {
  actions: TraceViewerActions;
  earlierError: string;
  earlierLoading: boolean;
  inspectorTab: TraceInspectorTab;
  rawEventId: string;
  resources: TraceViewerResources;
  selectedEventId: string;
}) {
  useSettingsPreferences();
  const detail = resources.detail.data;
  const selectedEvent = detail?.timeline.find((event) => event.id === selectedEventId) ?? null;
  const node: TraceInspectorNode = selectedEvent ? { kind: "event", value: selectedEvent } : null;
  return (
    <div className="trace-workspace" id="trace-events-panel" role="tabpanel">
      <main className="trace-workspace-primary" aria-busy={resources.detail.loading}>
        <div className="trace-surface-toolbar">
          <div><strong>{uiText("Event evidence")}</strong><span className="muted">Most recent bounded ledger window, oldest to newest.</span></div>
          {detail?.has_more ? (
            <button className="icon-button" disabled={earlierLoading} type="button" onClick={actions.loadEarlierEvents}>
              {earlierLoading ? "Loading…" : "Load earlier"}
            </button>
          ) : null}
        </div>
        <div className="trace-surface-scroll">
          {resources.detail.loading ? <TraceState>{uiText("Loading bounded event evidence…")}</TraceState> : null}
          {resources.detail.error ? <p className="trace-load-error" role="alert">Trace detail unavailable: {resources.detail.error}</p> : null}
          {earlierError ? <p className="trace-load-error" role="alert">Earlier events unavailable: {earlierError}</p> : null}
          {!resources.detail.loading && !resources.detail.error && detail && !detail.timeline.length ? <TraceState>{uiText("No events recorded for this trace.")}</TraceState> : null}
          {detail?.timeline.length ? (
            <ol className="trace-event-list">
              {detail.timeline.map((event, index) => (
                <TraceEventRow
                  event={event}
                  key={event.id || `seq-${event.seq ?? index}`}
                  selected={selectedEventId === event.id}
                  onSelect={actions.selectEvent}
                />
              ))}
            </ol>
          ) : null}
        </div>
      </main>
      <TraceInspector node={node} rawEventId={rawEventId} rawState={resources.raw} tab={inspectorTab} onTabChange={actions.selectInspectorTab} />
    </div>
  );
}

function TraceEventRow({
  event,
  onSelect,
  selected,
}: {
  event: TraceTimelineEvent;
  onSelect: (eventId: string) => void;
  selected: boolean;
}) {
  const summary = event.summary || event.type;
  return (
    <li>
      <button
        aria-current={selected ? "true" : undefined}
        className={selected ? "selected" : ""}
        type="button"
        onClick={() => onSelect(event.id || "")}
      >
        <span className="trace-event-time mono">{formatTimestamp(event.ts || "")}</span>
        <span className="trace-event-copy">
          <strong>{summary}</strong>
          {summary !== event.type ? <span className="mono muted">{event.type}</span> : null}
        </span>
        <span className="trace-event-actor">{event.actor || "system"}</span>
      </button>
    </li>
  );
}

function TraceState({ children }: { children: string }) {
  return <p className="trace-table-state muted">{children}</p>;
}

function stageKey(stage: string, firstSeq: number): string {
  return `${stage}:${firstSeq}`;
}
