import { useSettingsPreferences } from "../../settings/useSettingsPreferences";
import { uiText } from "../../settings/uiText";
import type {
  TraceEventDetail,
  TraceExecutionRouteStage,
  TraceLifecycleSpan,
  TraceTimelineEvent,
} from "../../api/types";
import { PreBlock } from "../../app/shared";
import { formatDuration, formatTimestamp, traceStatus } from "./traceModel";
import { spanDurationSeconds } from "./traceSpanModel";

export type TraceInspectorTab = "overview" | "raw";
export type TraceInspectorNode =
  | { kind: "span"; value: TraceLifecycleSpan }
  | { kind: "stage"; value: TraceExecutionRouteStage }
  | { kind: "event"; value: TraceTimelineEvent }
  | null;

export interface TraceRawState {
  data: TraceEventDetail | null;
  error: string;
  loading: boolean;
}

export function TraceInspector({
  node,
  onTabChange,
  rawEventId,
  rawState,
  tab,
}: {
  node: TraceInspectorNode;
  onTabChange: (tab: TraceInspectorTab) => void;
  rawEventId: string;
  rawState: TraceRawState;
  tab: TraceInspectorTab;
}) {
  useSettingsPreferences();
  return (
    <aside aria-label="Trace inspector" className="trace-inspector">
      <header className="trace-inspector-heading">
        <div>
          <span className="muted">{uiText("Inspector")}</span>
          <h4>{nodeLabel(node)}</h4>
        </div>
      </header>
      <div aria-label="Inspector view" className="trace-inspector-tabs" role="tablist">
        <button
          aria-controls="trace-inspector-overview"
          aria-selected={tab === "overview"}
          role="tab"
          type="button"
          onClick={() => onTabChange("overview")}
        >
          {uiText("Overview")}
        </button>
        <button
          aria-controls="trace-inspector-raw"
          aria-selected={tab === "raw"}
          disabled={!rawEventId}
          role="tab"
          type="button"
          onClick={() => onTabChange("raw")}
        >
          {uiText("Raw")}
        </button>
      </div>
      <div className="trace-inspector-body">
        {tab === "overview" ? (
          <div id="trace-inspector-overview" role="tabpanel">
            <TraceNodeOverview node={node} />
          </div>
        ) : (
          <div id="trace-inspector-raw" role="tabpanel">
            <TraceRawEvidence eventId={rawEventId} state={rawState} />
          </div>
        )}
      </div>
    </aside>
  );
}

function TraceNodeOverview({ node }: { node: TraceInspectorNode }) {
  useSettingsPreferences();
  if (!node) {
    return <p className="trace-inspector-empty muted">{uiText("Select a span, stage, or event to inspect its evidence.")}</p>;
  }
  if (node.kind === "span") return <SpanOverview span={node.value} />;
  if (node.kind === "stage") return <StageOverview stage={node.value} />;
  return <EventOverview event={node.value} />;
}

function SpanOverview({ span }: { span: TraceLifecycleSpan }) {
  const status = traceStatus({ status: span.status, last_type: "" });
  return (
    <div className="trace-inspector-overview">
      <InspectorStatus status={status} value={span.status} />
      <InspectorFacts facts={[
        ["Kind", span.kind],
        ["Duration", formatDuration(spanDurationSeconds(span))],
        ["Started", span.started_at ? formatTimestamp(span.started_at) : "Unavailable"],
        ["Ended", span.ended_at ? formatTimestamp(span.ended_at) : "Unavailable"],
        ["Source", span.source],
        ["Truth class", span.truth_class],
        ["Parent", span.parent_span_id || "Root"],
        ["Task", span.task_id || "—"],
        ["Actor", span.actor || "—"],
        ["Backend", span.backend || "—"],
      ]} />
      {span.degraded ? (
        <p className="trace-coverage-note" role="status">
          Degraded lifecycle evidence{span.degradation_reason ? `: ${span.degradation_reason}` : "."}
        </p>
      ) : null}
      <EvidenceList eventIds={span.source_event_ids} />
      {span.provenance && Object.keys(span.provenance).length ? (
        <section className="trace-inspector-section">
          <h5>Provenance</h5>
          <PreBlock value={span.provenance} />
        </section>
      ) : null}
    </div>
  );
}

function StageOverview({ stage }: { stage: TraceExecutionRouteStage }) {
  useSettingsPreferences();
  const status = traceStatus({ status: stage.status, last_type: "" });
  const duration = durationBetween(stage.first_ts, stage.last_ts);
  return (
    <div className="trace-inspector-overview">
      <InspectorStatus status={status} value={stage.status} />
      <InspectorFacts facts={[
        ["Duration", formatDuration(duration)],
        ["Started", formatTimestamp(stage.first_ts)],
        ["Ended", formatTimestamp(stage.last_ts)],
        ["Events", String(stage.event_count)],
        ["Failed events", String(stage.failed_count)],
        ["Execution", stage.parallel ? "Parallel" : "Sequential"],
      ]} />
      <ValueList label="Actors" values={stage.actors} />
      <ValueList label={uiText("Tasks")} values={stage.task_ids} />
      <ValueList label="Event types" values={stage.event_types} />
    </div>
  );
}

function EventOverview({ event }: { event: TraceTimelineEvent }) {
  const status = traceStatus({ status: event.status ?? "", last_type: event.type });
  return (
    <div className="trace-inspector-overview">
      <InspectorStatus status={status} value={event.status || "observed"} />
      <InspectorFacts facts={[
        ["Time", event.ts ? formatTimestamp(event.ts) : "Unknown"],
        ["Type", event.type],
        ["Actor", event.actor || "system"],
        ["Task", event.task_id || "—"],
        ["Sequence", event.seq == null ? "—" : String(event.seq)],
        ["Causation", event.causation_id || "—"],
        ["Correlation", event.correlation_id || "—"],
      ]} />
      {event.summary ? <p className="trace-event-summary">{event.summary}</p> : null}
    </div>
  );
}

function InspectorStatus({ status, value }: { status: string; value: string }) {
  return <span className={`trace-status trace-status-${status}`}>{value}</span>;
}

function InspectorFacts({ facts }: { facts: Array<[string, string]> }) {
  return (
    <dl className="trace-inspector-facts">
      {facts.map(([label, value]) => (
        <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  );
}

function EvidenceList({ eventIds }: { eventIds: string[] }) {
  useSettingsPreferences();
  return (
    <section className="trace-inspector-section">
      <h5>{uiText("Source evidence")}</h5>
      {eventIds.length ? (
        <ul className="trace-evidence-list">
          {eventIds.map((eventId) => <li className="mono" key={eventId}>{eventId}</li>)}
        </ul>
      ) : <p className="muted">{uiText("No source event links were recorded.")}</p>}
      {eventIds.length > 1 ? <p className="muted">Raw opens the first bounded source event.</p> : null}
    </section>
  );
}

function ValueList({ label, values }: { label: string; values: string[] }) {
  useSettingsPreferences();
  return (
    <section className="trace-inspector-section">
      <h5>{label}</h5>
      {values.length ? <p>{values.join(", ")}</p> : <p className="muted">{uiText("None recorded.")}</p>}
    </section>
  );
}

function TraceRawEvidence({ eventId, state }: { eventId: string; state: TraceRawState }) {
  useSettingsPreferences();
  if (!eventId) return <p className="trace-inspector-empty muted">Raw evidence is unavailable for this node.</p>;
  if (state.loading) return <p className="trace-inspector-empty muted">{uiText("Loading the redacted event record...")}</p>;
  if (state.error) return <p className="trace-load-error" role="alert">Raw event unavailable: {state.error}</p>;
  if (!state.data) return null;
  return (
    <section className="trace-raw-panel">
      <div className="trace-raw-heading">
        <h5>{uiText("Event")} {state.data.event_id}</h5>
        <span className="muted">redacted read-only</span>
      </div>
      <PreBlock value={state.data.event} />
    </section>
  );
}

function nodeLabel(node: TraceInspectorNode): string {
  if (!node) return "Nothing selected";
  if (node.kind === "span") return node.value.name || node.value.span_id;
  if (node.kind === "stage") return node.value.label || node.value.stage;
  return node.value.summary || node.value.type;
}

function durationBetween(first: string, last: string): number | null {
  const start = Date.parse(first);
  const end = Date.parse(last);
  return Number.isFinite(start) && Number.isFinite(end) && end >= start ? (end - start) / 1000 : null;
}
