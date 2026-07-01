// LifecycleDrawer (2026-06-11 S-B, T-刀②扩展) — task-lifecycle.v1 抽屉。
// 头部: task id + 状态 + dispatch-{短码}(点击 → Events tab 按该 try 过滤)。
// 六 tab: Trajectory(轨迹一行 + tries 行 + 可展开 gate detail)/ Events /
// Artifacts / Briefing / Contract / Usage(bodies 在 LifecycleDrawerTabs)。
import { Fragment, useMemo, useState } from "react";

import type { DeliveryTrace } from "../../api/types";
import { formatSeconds } from "../common/SegBar";
import { copyText, dtTone, latestDispatchTry, shortDispatch } from "./DeliveryTraceViewUtils";
import {
  GateBadge,
  LdArtifactsTab,
  LdBriefingTab,
  LdContractTab,
  LdEventsTab,
  LdUsageTab,
} from "./LifecycleDrawerTabs";

export type LifecycleDrawerTab =
  | "trajectory" | "events" | "artifacts" | "briefing" | "contract" | "usage";

const LC_TABS: LifecycleDrawerTab[] = [
  "trajectory", "events", "artifacts", "briefing", "contract", "usage",
];

interface LifecycleDrawerProps {
  initialTab?: LifecycleDrawerTab;
  initialTry?: number;
  onClose: () => void;
  taskId: string;
  trace: DeliveryTrace;
}

const STATE_TONE: Record<string, string> = {
  backlog: "none", ready: "ready", queued: "queued", running: "running",
  verify: "running", done: "done", failed: "failed", blocked: "blocked",
};

function dwellLabel(seconds?: number | null): string {
  return seconds === null || seconds === undefined ? "·" : formatSeconds(seconds);
}

// flow_metrics 当前 schema 无 tokens/cost 字段 — 防御性读取,字段出现即显示。
function numField(value: unknown, key: string): number | null {
  if (!value || typeof value !== "object") return null;
  const raw = (value as Record<string, unknown>)[key];
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

export function LifecycleDrawer({ initialTab, initialTry, onClose, taskId, trace }: LifecycleDrawerProps) {
  const [tab, setTab] = useState<LifecycleDrawerTab>(initialTab ?? "trajectory");
  const [selectedTry, setSelectedTry] = useState<number | null>(initialTry ?? null);
  const [expandedGate, setExpandedGate] = useState("");
  const entry = trace.task_lifecycle?.tasks?.[taskId];
  const node = trace.execution_graph?.nodes?.find((n) => n.task_id === taskId);
  const metrics = trace.flow_metrics?.tasks?.[taskId];
  const history = entry?.state_history ?? [];
  const tries = entry?.tries ?? [];
  const gates = useMemo(
    () => tries.flatMap((t) => t.gate_results.map((gate) => ({ ...gate, tryNumber: t.try }))),
    [tries],
  );
  const taskSpans = useMemo(
    () => (trace.trace?.spans ?? []).filter((span) => span.task_id === taskId),
    [taskId, trace],
  );
  const spanTokens = useMemo(() => {
    let input = 0;
    let output = 0;
    let found = false;
    for (const span of taskSpans) {
      input += span.tokens_input ?? 0;
      output += span.tokens_output ?? 0;
      found = true;
    }
    return found ? { input, output } : null;
  }, [taskSpans]);
  const costUsd = numField(metrics, "cost_usd") ?? numField(metrics, "usd");
  const tokensIn = numField(metrics, "tokens_input") ?? spanTokens?.input ?? null;
  const tokensOut = numField(metrics, "tokens_output") ?? spanTokens?.output ?? null;
  const status = node?.actual.status || history[history.length - 1]?.state || "unknown";
  const dispatchTry = latestDispatchTry(tries);

  // item 10 — expanded gate chip resolves back to its gate_results entry.
  const expandedGateResult = useMemo(() => {
    if (!expandedGate) return null;
    const [tryPart, idxPart] = expandedGate.split(":");
    const owner = tries.find((item) => String(item.try) === tryPart);
    return owner?.gate_results[Number(idxPart)] ?? null;
  }, [expandedGate, tries]);

  const empty = history.length === 0 && tries.length === 0;

  return (
    <section className="lifecycle-drawer" data-testid="lifecycle-drawer" aria-label={`Task lifecycle ${taskId}`}>
      <div className="ld-head">
        <span className="ld-task-id" title={taskId}>{taskId}</span>
        <span className={`badge badge-${dtTone(status)}`}>{status}</span>
        {node?.superseded && <span className="badge">▒ superseded</span>}
        {dispatchTry?.dispatch_id && (
          <span className="ld-dispatch-wrap">
            <button
              type="button"
              className="ld-dispatch"
              data-testid="ld-dispatch"
              title={`${dispatchTry.dispatch_id} — open Events tab for try#${dispatchTry.try} seq range`}
              onClick={() => {
                setSelectedTry(dispatchTry.try);
                setTab("events");
              }}
            >
              dispatch-{shortDispatch(dispatchTry.dispatch_id)}
            </button>
            <button
              type="button"
              className="ld-copy-icon"
              title={`copy ${dispatchTry.dispatch_id}`}
              aria-label="copy dispatch id"
              onClick={() => copyText(dispatchTry.dispatch_id!)}
            >
              ⧉
            </button>
          </span>
        )}
        <button type="button" className="ld-close" onClick={onClose} aria-label="close lifecycle drawer">✕</button>
      </div>
      <div className="ld-tabs" role="tablist" aria-label="Task lifecycle views">
        {LC_TABS.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={tab === name}
            className={`ld-tab${tab === name ? " active" : ""}`}
            data-testid={`lc-tab-${name}`}
            onClick={() => setTab(name)}
          >
            {name.charAt(0).toUpperCase() + name.slice(1)}
          </button>
        ))}
      </div>
      {tab === "trajectory" && (
        empty ? (
          <p className="ld-empty">
            No lifecycle history for this task yet — task-lifecycle.v1 fills in once task events land.
          </p>
        ) : (
          <div className="ld-main">
            <div className="ld-traj" data-testid="ld-trajectory">
              {history.map((item, index) => {
                const prev = history[index - 1];
                const tryBoundary = item.try != null && item.try !== prev?.try;
                return (
                  <Fragment key={`${item.state}-${index}`}>
                    {index > 0 && (
                      <span className="ld-arrow" aria-hidden="true">─{dwellLabel(prev?.dwell_seconds)}─▶</span>
                    )}
                    {tryBoundary && <span className="ld-try-mark">try#{item.try}</span>}
                    <span
                      className={`ld-state rg-state-${STATE_TONE[item.state] ?? "none"}`}
                      title={`${item.state} · entered ${item.entered_at ?? "—"} · dwell ${dwellLabel(item.dwell_seconds)} · ${item.via_event_id ?? ""}`}
                    >
                      {item.state}
                    </span>
                  </Fragment>
                );
              })}
              {history.length === 0 && <span className="muted">no state history</span>}
            </div>
            <div className="ld-tries" data-testid="ld-tries">
              {tries.map((tryItem, index) => (
                <span key={tryItem.try} className="ld-try">
                  {index > 0 && <span className="muted" aria-hidden="true">·</span>}
                  <span className="ld-try-mark">#{tryItem.try}</span>
                  <span>resp {formatSeconds(tryItem.first_response_seconds)}</span>
                  <span>→ {tryItem.outcome}</span>
                  {tryItem.rework_kind && <span className="badge badge-warn">{tryItem.rework_kind}</span>}
                  {tryItem.gate_results.map((gate, gateIndex) => {
                    const key = `${tryItem.try}:${gateIndex}`;
                    return (
                      <GateBadge
                        active={expandedGate === key}
                        gate={gate}
                        key={gateIndex}
                        onToggle={() => setExpandedGate((prev) => (prev === key ? "" : key))}
                      />
                    );
                  })}
                </span>
              ))}
              {tries.length === 0 && <span className="muted">no dispatch tries recorded</span>}
            </div>
            {expandedGateResult && (
              <div className="ld-gate-detail" data-testid="ld-gate-detail">
                <span className="ld-gate-detail-head">{expandedGateResult.type}</span>
                {Object.keys(expandedGateResult.detail ?? {}).length ? (
                  <dl className="ld-gate-detail-grid">
                    {Object.entries(expandedGateResult.detail!).map(([key, value]) => (
                      <Fragment key={key}>
                        <dt title={key}>{key}</dt>
                        <dd title={String(value)}>{String(value)}</dd>
                      </Fragment>
                    ))}
                  </dl>
                ) : (
                  <span className="muted">no detail</span>
                )}
              </div>
            )}
          </div>
        )
      )}
      {tab === "events" && (
        <LdEventsTab
          selectedTry={selectedTry}
          setSelectedTry={setSelectedTry}
          spans={taskSpans}
          tries={tries}
        />
      )}
      {tab === "artifacts" && <LdArtifactsTab gates={gates} traceId={trace.trace_id} tries={tries} />}
      {tab === "briefing" && <LdBriefingTab tries={tries} />}
      {tab === "contract" && <LdContractTab node={node} />}
      {tab === "usage" && (
        <LdUsageTab costUsd={costUsd} tokensIn={tokensIn} tokensOut={tokensOut} tries={tries} />
      )}
    </section>
  );
}
