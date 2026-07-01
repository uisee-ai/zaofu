import { GitFork, Map as MapIcon, Route } from "lucide-react";
import type { ReactNode } from "react";

import type { DeliveryRunTraceSpan, DeliveryTrace } from "../../api/types";
import type { PageId } from "../../app/sharedTypes";
import { dtTone } from "./DeliveryTraceViewUtils";

interface DeliveryOverviewProps {
  onOpenPage?: (page: PageId) => void;
  trace: DeliveryTrace;
}

interface AttentionItem {
  label: string;
  meta: string;
  tone: "ok" | "warn" | "err" | "info" | "muted";
}

export function DeliveryOverview({ onOpenPage, trace }: DeliveryOverviewProps) {
  const tasks = taskSummary(trace);
  const run = runSummary(trace);
  const loop = loopSummary(trace);
  const attention = attentionItems(trace);

  return (
    <section className="delivery-overview" data-testid="delivery-overview">
      <div className="delivery-overview-actions" aria-label="Delivery drilldown actions">
        <button className="delivery-overview-action" type="button" onClick={() => onOpenPage?.("delivery-trace")}>
          <Route size={15} strokeWidth={1.8} aria-hidden="true" />
          <span>Open Trace</span>
        </button>
        <button className="delivery-overview-action" type="button" onClick={() => onOpenPage?.("behavior-loop")}>
          <GitFork size={15} strokeWidth={1.8} aria-hidden="true" />
          <span>Open Loop</span>
        </button>
        <button className="delivery-overview-action" type="button" onClick={() => onOpenPage?.("delivery-graph")}>
          <MapIcon size={15} strokeWidth={1.8} aria-hidden="true" />
          <span>Open Graph</span>
        </button>
      </div>

      <div className="delivery-overview-grid">
        <OverviewCard title="Attention" meta={`${attention.length} signals`}>
          {attention.length ? (
            <div className="delivery-attention-list">
              {attention.slice(0, 5).map((item, index) => (
                <div className={`delivery-attention-row tone-${item.tone}`} key={`${item.label}-${index}`}>
                  <strong>{item.label}</strong>
                  <span>{item.meta}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted">No blocking delivery signals.</p>
          )}
        </OverviewCard>

        <OverviewCard title="Task Progress" meta={`${tasks.total} tasks`}>
          <div className="delivery-progress-bar" aria-label="Task progress">
            <span className="done" style={{ width: `${tasks.donePct}%` }} />
            <span className="active" style={{ width: `${tasks.activePct}%` }} />
            <span className="blocked" style={{ width: `${tasks.blockedPct}%` }} />
          </div>
          <div className="delivery-overview-metrics">
            <Metric label="Done" value={tasks.done} />
            <Metric label="Running" value={tasks.active} />
            <Metric label="Blocked" value={tasks.blocked} tone={tasks.blocked ? "err" : "muted"} />
            <Metric label="Waiting" value={tasks.waiting} />
          </div>
        </OverviewCard>

        <OverviewCard title="Latest Run" meta={`${run.spans} spans`}>
          <div className="delivery-overview-metrics">
            <Metric label="Running" value={run.running} tone={run.running ? "info" : "muted"} />
            <Metric label="Failed" value={run.failed} tone={run.failed ? "err" : "muted"} />
            <Metric label="Passed" value={run.passed} tone={run.passed ? "ok" : "muted"} />
          </div>
          <p className="delivery-overview-note">{run.latestLabel}</p>
        </OverviewCard>

        <OverviewCard title="Latest Loop" meta={`${loop.related} related`}>
          <div className="delivery-overview-metrics">
            <Metric label="Behaviors" value={loop.behaviors} tone={loop.behaviors ? "warn" : "muted"} />
            <Metric label="Evals" value={loop.evals} tone={loop.failedEvals ? "err" : "muted"} />
            <Metric label="Candidates" value={loop.candidates} tone={loop.candidates ? "info" : "muted"} />
          </div>
          <p className="delivery-overview-note">{loop.replanLabel}</p>
        </OverviewCard>
      </div>
    </section>
  );
}

function OverviewCard({
  children,
  meta,
  title,
}: {
  children: ReactNode;
  meta: string;
  title: string;
}) {
  return (
    <article className="delivery-overview-card">
      <div className="delivery-overview-card-head">
        <h3>{title}</h3>
        <span>{meta}</span>
      </div>
      {children}
    </article>
  );
}

function Metric({
  label,
  tone = "info",
  value,
}: {
  label: string;
  tone?: "ok" | "warn" | "err" | "info" | "muted";
  value: number | string;
}) {
  return (
    <div className={`delivery-overview-metric tone-${tone}`}>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function taskSummary(trace: DeliveryTrace) {
  const total = trace.execution_graph.task_count || trace.execution_graph.nodes.length || 0;
  const done = trace.execution_graph.done_count || countTasks(trace, ["done", "passed"]);
  const active = trace.execution_graph.in_progress_count || countTasks(trace, ["in_progress", "running"]);
  const blocked = trace.execution_graph.blocked_count || countTasks(trace, ["blocked", "failed"]);
  const waiting = Math.max(0, total - done - active - blocked);
  const denom = Math.max(1, total);
  return {
    active,
    activePct: pct(active, denom),
    blocked,
    blockedPct: pct(blocked, denom),
    done,
    donePct: pct(done, denom),
    total,
    waiting,
  };
}

function runSummary(trace: DeliveryTrace) {
  const spans = ((trace.trace?.spans ?? trace.thick_trace?.spans ?? []) as DeliveryRunTraceSpan[]);
  const latest = spans[spans.length - 1];
  return {
    failed: spans.filter((span) => ["failed", "error", "blocked"].includes(span.status)).length,
    latestLabel: latest ? `${latest.name || latest.span_id} · ${latest.status || "observed"}` : "No runtime spans projected yet.",
    passed: spans.filter((span) => ["passed", "done", "ok"].includes(span.status)).length,
    running: spans.filter((span) => ["running", "in_progress"].includes(span.status)).length,
    spans: spans.length,
  };
}

function loopSummary(trace: DeliveryTrace) {
  const thick = trace.thick_trace;
  const failedEvals = (thick?.evals ?? []).filter((item) => ["failed", "error", "blocked"].includes(item.status)).length;
  const replan = trace.deposition_summary?.replan_gate_status || "";
  return {
    behaviors: thick?.behaviors.length ?? 0,
    candidates: thick?.improvement_candidates.length ?? 0,
    evals: thick?.evals.length ?? 0,
    failedEvals,
    related: trace.related_loop_count ?? trace.related_loop_ids?.length ?? 0,
    replanLabel: replan && replan !== "none" ? `Replan ${replan}` : "No active replan gate.",
  };
}

function attentionItems(trace: DeliveryTrace): AttentionItem[] {
  const out: AttentionItem[] = [];
  if (["blocked", "failed", "error"].includes(trace.ship.status)) {
    out.push({ label: `Ship ${trace.ship.status}`, meta: trace.ship.readiness || "release gate blocked", tone: "err" });
  }
  for (const item of trace.ship.missing_evidence ?? []) {
    out.push({ label: "Missing evidence", meta: `${item.task_id} · ${item.status}`, tone: "err" });
  }
  for (const item of trace.thick_trace?.behaviors ?? []) {
    if (["failed", "open", "blocked"].includes(item.status)) {
      out.push({ label: item.kind, meta: item.summary || item.detector || "behavior signal", tone: item.status === "failed" ? "err" : "warn" });
    }
  }
  for (const item of trace.thick_trace?.evals ?? []) {
    if (["failed", "open", "blocked"].includes(item.status)) {
      out.push({ label: item.kind, meta: item.evaluator || item.owner_event_type || "eval signal", tone: "err" });
    }
  }
  if (trace.drift_report.items.length > 0) {
    out.push({ label: `Drift ${trace.drift_report.status}`, meta: `${trace.drift_report.items.length} drift items`, tone: "warn" });
  }
  for (const diagnostic of trace.diagnostics ?? []) {
    out.push({ label: diagnostic.kind, meta: diagnostic.message, tone: dtTone(diagnostic.kind) });
  }
  return out;
}

function countTasks(trace: DeliveryTrace, statuses: string[]) {
  return trace.execution_graph.nodes.filter((node) => statuses.includes(node.actual.status)).length;
}

function pct(value: number, total: number) {
  return Math.max(0, Math.min(100, Math.round((value / total) * 100)));
}
