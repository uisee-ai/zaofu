import { useSettingsPreferences } from "../../settings/useSettingsPreferences";
import { uiText } from "../../settings/uiText";
// LifecycleDrawerTabs (T-刀②) — Events / Artifacts / Briefing / Contract /
// Usage tab bodies for the lifecycle drawer, plus the shared GateBadge.
// 全部只读、空态降级:有什么数据渲染什么,缺数据显式说明,不造假。
import type {
  DeliveryTaskGateResult,
  DeliveryTaskLifecycleState,
  DeliveryTaskTry,
  DeliveryTraceNode,
} from "../../api/types";
import { copyText, seqRangeLabel } from "./DeliveryTraceViewUtils";

export type GateWithTry = DeliveryTaskGateResult & { tryNumber: number };

export function shortGateType(gateType: string): string {
  return gateType.split(".")[0] || gateType;
}

// Gate badge — with onToggle it becomes the item-10 expandable detail chip.
export function GateBadge({
  active,
  gate,
  onToggle,
}: {
  active?: boolean;
  gate: DeliveryTaskGateResult;
  onToggle?: () => void;
}) {
  const label = `${shortGateType(gate.type)} ${gate.passed ? "✓" : "✗"}`;
  const title = `${gate.type} ${gate.passed ? "passed" : "failed"}${gate.event_id ? ` · ${gate.event_id}` : ""}${onToggle ? " — click for detail" : ""}`;
  if (!onToggle) {
    return <span className={`ld-gate ${gate.passed ? "is-pass" : "is-fail"}`} title={title}>{label}</span>;
  }
  return (
    <button
      type="button"
      className={`ld-gate ld-gate-btn ${gate.passed ? "is-pass" : "is-fail"}${active ? " is-open" : ""}`}
      title={title}
      onClick={onToggle}
      aria-expanded={!!active}
    >
      {label}
    </button>
  );
}

function CopyIcon({ label, value }: { label: string; value: string }) {
  return (
    <button
      type="button"
      className="ld-copy-icon"
      title={`copy ${value}`}
      aria-label={label}
      onClick={() => copyText(value)}
    >
      ⧉
    </button>
  );
}

// Events — bounded refs already carried by task-lifecycle.v1. The Runs view
// does not request the Trace span projection.
export function LdEventsTab({
  stateHistory,
  selectedTry,
  setSelectedTry,
  tries,
}: {
  stateHistory: DeliveryTaskLifecycleState[];
  selectedTry: number | null;
  setSelectedTry: (tryNumber: number) => void;
  tries: DeliveryTaskTry[];
}) {
  if (!tries.length) {
    return <p className="ld-tab-empty">No dispatch tries recorded — seq ranges appear once dispatches land.</p>;
  }
  const current = tries.find((item) => item.try === selectedTry) ?? tries[tries.length - 1];
  const seq = seqRangeLabel(current.seq_first, current.seq_last);
  const eventRefs = Array.from(new Set([
    current.dispatch_id,
    ...stateHistory.filter((item) => item.try == null || item.try === current.try).map((item) => item.via_event_id),
    ...current.gate_results.map((gate) => gate.event_id),
  ].filter((value): value is string => Boolean(value))));
  return (
    <div className="ld-tab-body" data-testid="ld-events">
      <div className="ld-try-chips">
        {tries.map((item) => (
          <button
            key={item.try}
            type="button"
            className={`ld-chip${item.try === current.try ? " active" : ""}`}
            onClick={() => setSelectedTry(item.try)}
          >
            #{item.try}
          </button>
        ))}
        <span
          className="ld-seq"
          title={seq ? `events for try#${current.try} live in this seq range` : "no seq range recorded for this try"}
        >
          {seq ?? "seq[—]"}
        </span>
        {seq && (
          <CopyIcon
            label={`copy seq range for try ${current.try}`}
            value={`${current.seq_first ?? ""}..${current.seq_last ?? ""}`}
          />
        )}
      </div>
      {eventRefs.length ? (
        <div className="ld-event-list">
          {eventRefs.slice(0, 40).map((eventId) => (
            <div key={eventId} className="ld-evidence-row">
              <code title={eventId}>{eventId}</code>
              <CopyIcon label={`copy event ref ${eventId}`} value={eventId} />
            </div>
          ))}
        </div>
      ) : (
        <p className="ld-tab-empty">No event refs recorded for this try.</p>
      )}
    </div>
  );
}

// Artifacts — gate evidence (migrated from the old evidence aside) +
// per-try snapshot refs + trace-id copy.
export function LdArtifactsTab({
  gates,
  traceId,
  tries,
}: {
  gates: GateWithTry[];
  traceId: string;
  tries: DeliveryTaskTry[];
}) {
  useSettingsPreferences();
  const snapshots = tries.filter((item) => item.snapshot_ref);
  return (
    <div className="ld-tab-body" data-testid="ld-artifacts">
      {gates.length ? (
        gates.slice(-8).map((gate, index) => (
          <div className="ld-evidence-row" key={index}>
            <span className="ld-try-mark">#{gate.tryNumber}</span>
            <GateBadge gate={gate} />
            <code title={gate.event_id ?? ""}>{gate.event_id || "—"}</code>
          </div>
        ))
      ) : (
        <span className="muted">{uiText("No gate evidence.")}</span>
      )}
      {snapshots.map((item) => (
        <div className="ld-evidence-row" key={`snap-${item.try}`}>
          <span className="ld-try-mark">#{item.try}</span>
          <span className="muted">snapshot</span>
          <code title={item.snapshot_ref!}>{item.snapshot_ref}</code>
          <CopyIcon label={`copy snapshot ref for try ${item.try}`} value={item.snapshot_ref!} />
        </div>
      ))}
      {!snapshots.length && <span className="muted">No snapshot refs on tries.</span>}
      {traceId ? (
        <button type="button" className="ld-copy" onClick={() => copyText(traceId)} title={traceId}>
          copy trace id
        </button>
      ) : null}
    </div>
  );
}

// Briefing — path display + copy only; the web UI never reads file contents.
export function LdBriefingTab({ tries }: { tries: DeliveryTaskTry[] }) {
  const rows = tries.filter((item) => item.briefing_ref);
  if (!rows.length) {
    return <p className="ld-tab-empty">No briefing refs recorded on tries.</p>;
  }
  return (
    <div className="ld-tab-body" data-testid="ld-briefing">
      {rows.map((item) => (
        <div className="ld-evidence-row" key={item.try}>
          <span className="ld-try-mark">#{item.try}</span>
          <code title={item.briefing_ref!}>{item.briefing_ref}</code>
          <CopyIcon label={`copy briefing path for try ${item.try}`} value={item.briefing_ref!} />
        </div>
      ))}
      <span className="muted">paths only — contents are not read by the web UI</span>
    </div>
  );
}

// Contract — render only what the execution-graph node actually carries.
export function LdContractTab({ node }: { node?: DeliveryTraceNode }) {
  useSettingsPreferences();
  const raw = node as unknown as Record<string, unknown> | undefined;
  const contract = raw?.contract ?? raw?.capsule ?? null;
  const empty = contract == null
    || (typeof contract === "object" && Object.keys(contract as object).length === 0);
  if (empty) {
    return <p className="ld-tab-empty">{uiText("No contract/capsule carried on the execution-graph node.")}</p>;
  }
  return (
    <div className="ld-tab-body" data-testid="ld-contract">
      <pre className="delivery-raw-block">
        {typeof contract === "string" ? contract : JSON.stringify(contract, null, 2)}
      </pre>
    </div>
  );
}

// Usage — per-try tool_calls / tokens_in / tokens_out rows, plus the
// pre-existing task aggregate from flow metrics when available.
export function LdUsageTab({
  costUsd,
  tokensIn,
  tokensOut,
  tries,
}: {
  costUsd: number | null;
  tokensIn: number | null;
  tokensOut: number | null;
  tries: DeliveryTaskTry[];
}) {
  useSettingsPreferences();
  const rows = tries.filter(
    (item) => item.tool_calls != null || item.tokens_in != null || item.tokens_out != null,
  );
  const hasAggregate = tokensIn !== null || tokensOut !== null || costUsd !== null;
  if (!rows.length && !hasAggregate) {
    return <p className="ld-tab-empty">{uiText("No usage recorded for this task.")}</p>;
  }
  return (
    <div className="ld-tab-body" data-testid="ld-usage">
      {rows.map((item) => (
        <div className="ld-usage-row" key={item.try}>
          <span className="ld-try-mark">#{item.try}</span>
          <span className="ld-num">tools {item.tool_calls ?? "—"}</span>
          <span className="ld-num">{item.tokens_in ?? "—"} in / {item.tokens_out ?? "—"} out</span>
        </div>
      ))}
      {hasAggregate && (
        <div className="ld-usage-row ld-usage-total">
          <span className="muted">Σ task</span>
          {(tokensIn !== null || tokensOut !== null) && (
            <span className="ld-num">{tokensIn ?? 0} in / {tokensOut ?? 0} out</span>
          )}
          {costUsd !== null && <span className="ld-num">${costUsd.toFixed(4)}</span>}
        </div>
      )}
    </div>
  );
}
