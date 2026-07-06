// AutomationsPage + exclusive closure, extracted verbatim from App.tsx (P1 split).
import type { ActionResponse } from "../../api/types";
import { Bell, CalendarClock, FileText, PlayCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { ProjectionEmptyState, TablePage, asRecord, automationShortId, automationShortRunId, automationStatusTone, numberValue, projectLabelFromId, recordValue, stringify, textValue } from "../../app/shared";
import { previewItemsFromRefs } from "../agent-session/previewRegistry";

interface AutomationRunFeedback {
  automationId: string;
  eventId: string;
  ok: boolean;
  runId: string;
  status: string;
  message: string;
}


export function AutomationsPage({
  actionReady,
  actionState,
  automations,
  onRun,
}: {
  actionReady: boolean;
  actionState: string;
  automations: Record<string, unknown> | null;
  onRun: (automationId: string) => Promise<ActionResponse>;
}) {
  const items = Array.isArray(automations?.items)
    ? automations.items.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
  const [selectedId, setSelectedId] = useState("daily-brief");
  useEffect(() => {
    if (items.length === 0) return;
    if (items.some((item) => String(item.automation_id) === selectedId)) return;
    setSelectedId(String(items[0].automation_id || ""));
  }, [items, selectedId]);
  const selected = items.find((item) => String(item.automation_id) === selectedId) ?? items[0] ?? null;
  const outputs = Array.isArray(selected?.outputs)
    ? selected.outputs.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
  const report = outputs.find((output) => String(output.type) === "report") ?? outputs[0] ?? null;
  const archetypeMatrix = asRecord(report?.archetype_matrix);
  const scorecard = asRecord(report?.scorecard);
  const decisionPanel = asRecord(report?.decision_panel);
  const insights = outputs.flatMap((output) => (
    Array.isArray(output.insights)
      ? output.insights.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
      : []
  ));
  const runs = Array.isArray(selected?.recent_runs)
    ? selected.recent_runs.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
  const allRuns = Array.isArray(selected?.all_runs)
    ? selected.all_runs.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : runs;
  const runCounts = Array.isArray(selected?.run_counts_by_day)
    ? selected.run_counts_by_day.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
  const runSummary = asRecord(selected?.run_counts_summary);
  const proposals = Array.isArray(selected?.proposals)
    ? selected.proposals.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
  const [runningId, setRunningId] = useState("");
  const [runFeedback, setRunFeedback] = useState<AutomationRunFeedback | null>(null);
  const selectedAutomationId = String(selected?.automation_id || "");
  const selectedRunning = Boolean(selectedAutomationId && runningId === selectedAutomationId);
  const selectedFeedback = runFeedback?.automationId === selectedAutomationId ? runFeedback : null;
  const selectedTitle = textValue(selected?.title || selected?.automation_id) || "Automation";
  const selectedStatus = textValue(selected?.status) || "idle";
  const selectedWindow = textValue(selected?.window) || "-";
  const lastRunId = textValue(asRecord(selected?.last_run).run_id);
  const lastRunLabel = lastRunId ? automationShortRunId(lastRunId) : "none";
  const nextRunLabel = textValue(selected?.next_run) || "manual";
  const triggerLabels = automationTriggerLabels(selected?.trigger);
  const successLabel = automationSuccessLabel(runSummary);
  const projectId = textValue(automations?.project_id);
  const projectLabel = projectLabelFromId(projectId) || projectId || "-";

  async function runSelectedAutomation() {
    const automationId = selectedAutomationId;
    if (!automationId || runningId) return;
    setRunningId(automationId);
    setRunFeedback({
      automationId,
      eventId: "",
      ok: true,
      runId: "",
      status: "running",
      message: "Manual run requested. Waiting for automation output...",
    });
    try {
      const result = await onRun(automationId);
      const resultRecord = recordValue(result) ?? {};
      const ok = result.ok !== false;
      const status = String(result.status || (ok ? "completed" : "failed"));
      const reason = textValue(result.reason).trim();
      setRunFeedback({
        automationId,
        eventId: textValue(resultRecord.event_id),
        ok,
        runId: textValue(resultRecord.run_id),
        status,
        message: reason || (ok ? "Automation run completed." : "Automation run failed."),
      });
    } catch (err) {
      setRunFeedback({
        automationId,
        eventId: "",
        ok: false,
        runId: "",
        status: "failed",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setRunningId("");
    }
  }

  return (
    <>
      <div className="section-heading">
        <div>
          <h2>Automations</h2>
          <span className="muted">Project-scoped reports, alerts, and proposals</span>
        </div>
        <span className="metric-chip">projection only</span>
      </div>
      <div className="tab-row compact-tabs">
        {items.map((item) => (
          <button
            className={`tab-button ${String(item.automation_id) === selectedId ? "active" : ""}`}
            key={String(item.automation_id)}
            type="button"
            onClick={() => setSelectedId(String(item.automation_id))}
          >
            {String(item.title || item.automation_id)}
          </button>
        ))}
      </div>
      {selected ? (
        <div className="automation-dashboard">
          <AutomationGlance
            actionReady={actionReady}
            actionState={actionState}
            insights={insights}
            outputs={outputs}
            proposals={proposals}
            runSummary={runSummary}
            selected={selected}
          />
          <AutomationReportStats
            insights={insights}
            proposals={proposals}
            runSummary={runSummary}
            selected={selected}
            successLabel={successLabel}
          />
          <AutomationDecisionPanel panel={decisionPanel} />
          <AutomationArchetypeMatrix matrix={archetypeMatrix} />
          <AutomationScorecard scorecard={scorecard} />
          <section className="subsection automation-control-section">
            <div className="automation-control-head">
              <div className="automation-control-title">
                <h3>{selectedTitle}</h3>
                <span className="muted">Manual trigger and current execution health</span>
              </div>
              <button
                className="icon-button primary"
                disabled={!actionReady || selectedRunning}
                title={actionReady ? "Run this automation now" : actionState}
                type="button"
                onClick={() => void runSelectedAutomation()}
              >
                <PlayCircle size={16} />
                {selectedRunning ? "Running" : "Run now"}
              </button>
            </div>
            {selectedFeedback ? (
              <div className={`notice automation-run-notice ${selectedFeedback.ok ? "notice-ok" : ""}`}>
                <span className="mono">{selectedFeedback.status}</span>
                {selectedFeedback.runId ? <span className="mono">{selectedFeedback.runId}</span> : null}
                <span>{selectedFeedback.message}</span>
                {selectedFeedback.eventId ? <span className="mono">event {selectedFeedback.eventId}</span> : null}
              </div>
            ) : null}
            <div className="automation-control-grid">
              <section className="automation-detail-card">
                <span className="eyebrow">Schedule</span>
                <dl>
                  <dt>Window</dt>
                  <dd>{selectedWindow}</dd>
                  <dt>Next Run</dt>
                  <dd>{nextRunLabel}</dd>
                  <dt>Trigger</dt>
                  <dd>
                    <span className="automation-trigger-list">
                      {triggerLabels.map((trigger) => (
                        <span className="automation-trigger-chip" key={trigger}>{trigger}</span>
                      ))}
                    </span>
                  </dd>
                </dl>
              </section>
              <section className="automation-detail-card">
                <span className="eyebrow">Execution Health</span>
                <dl>
                  <dt>Status</dt>
                  <dd><span className={`badge badge-${automationStatusTone(selectedStatus)}`}>{selectedStatus}</span></dd>
                  <dt>Last Run</dt>
                  <dd className="mono" title={lastRunId || "none"}>{lastRunLabel}</dd>
                  <dt>Success Rate</dt>
                  <dd>{successLabel}</dd>
                  <dt>Terminal Runs</dt>
                  <dd>{stringify(runSummary.terminal_total ?? 0)}</dd>
                </dl>
              </section>
              <section className="automation-detail-card">
                <span className="eyebrow">Project</span>
                <dl>
                  <dt>Workspace Project</dt>
                  <dd>
                    <span>{projectLabel}</span>
                    {projectId && projectId !== projectLabel ? <small className="mono">{projectId}</small> : null}
                  </dd>
                  <dt>Run Events</dt>
                  <dd>{stringify(runSummary.events_total ?? 0)}</dd>
                  <dt>Outputs</dt>
                  <dd>{outputs.length}</dd>
                  <dt>Proposals</dt>
                  <dd>{proposals.length}</dd>
                </dl>
              </section>
            </div>
          </section>
          <AutomationReportNav />
          <section className="subsection automation-insight-section" id="automation-insights">
            <div className="inline-heading">
              <h3>Insights</h3>
              <span className="muted">{insights.length}</span>
            </div>
            <AutomationInsights insights={insights} />
          </section>
          <AutomationOutputs outputs={outputs} />
          <div id="automation-runs" className="automation-runs-grid">
            <AutomationRunCounts rows={runCounts} />
            <AutomationRecentRuns runs={runs} />
          </div>
          {allRuns.length > runs.length ? <TablePage title="Run Ledger" rows={allRuns} embedded /> : null}
          <div id="automation-proposals" className="automation-anchor-target">
            <TablePage title="Proposals" rows={proposals} embedded />
          </div>
        </div>
      ) : (
        <section className="subsection automation-empty-section">
          <ProjectionEmptyState
            state={{
              title: "No automation projection",
              description: "This project has no daily brief, weekly review, or monitor projection yet.",
              icon: CalendarClock,
              compact: false,
            }}
          />
        </section>
      )}
    </>
  );
}


function AutomationGlance({
  actionReady,
  actionState,
  insights,
  outputs,
  proposals,
  runSummary,
  selected,
}: {
  actionReady: boolean;
  actionState: string;
  insights: Record<string, unknown>[];
  outputs: Record<string, unknown>[];
  proposals: Record<string, unknown>[];
  runSummary: Record<string, unknown>;
  selected: Record<string, unknown>;
}) {
  const title = String(selected.title || selected.automation_id || "Automation");
  const status = String(selected.status || "idle");
  const windowLabel = String(selected.window || "current");
  const latestOutput = outputs.find((output) => String(output.summary || "").trim()) ?? outputs[0];
  const latestSummary = latestOutput
    ? String(latestOutput.summary || "Projection produced an output without a summary.")
    : "No output projection has been generated yet.";
  const criticalCount = insights.filter((insight) => automationInsightTone(String(insight.severity || "")) === "err").length;
  const warningCount = insights.filter((insight) => automationInsightTone(String(insight.severity || "")) === "warn").length;
  const topAttention = automationTopAttention(insights);
  const suggestedActions = automationSuggestedActions(insights);
  const successRate = automationSuccessLabel(runSummary);
  const lastRunId = textValue(asRecord(selected.last_run).run_id);
  const lastRunLabel = lastRunId ? automationShortRunId(lastRunId) : "none";
  const actionMode = actionReady ? "manual run is available" : actionState;
  const tone = automationGlanceTone(insights);
  return (
    <section className={`automation-glance-section tone-${tone}`}>
      <div className="automation-glance-head">
        <span className={`badge badge-${tone}`}>At a Glance</span>
        <span className="muted">{title} · {windowLabel}</span>
      </div>
      <p>
        <strong>What's working:</strong> {title} is {status}; latest output says {latestSummary}
      </p>
      <p>
        <strong>What needs attention:</strong> {topAttention
          ? `${criticalCount} critical and ${warningCount} warning insight(s). Top signal: ${String(topAttention.title || "Insight")} - ${String(topAttention.summary || "-")}`
          : "No warning-level automation insight is currently projected."}
      </p>
      <p>
        <strong>Quick wins to try:</strong> {suggestedActions.length
          ? suggestedActions.join(" ")
          : proposals.length
            ? `${proposals.length} proposal(s) are ready for operator triage.`
            : "Keep the projection current and review the next run after major task movement."}
      </p>
      <p>
        <strong>Operating mode:</strong> last run {lastRunLabel}; success rate {successRate}; {actionMode}.
      </p>
    </section>
  );
}


function AutomationReportNav() {
  const links = [
    ["#automation-insights", "Insights"],
    ["#automation-outputs", "Outputs"],
    ["#automation-runs", "Runs"],
    ["#automation-proposals", "Proposals"],
  ];
  return (
    <nav className="automation-report-nav" aria-label="Automation report sections">
      {links.map(([href, label]) => (
        <a href={href} key={href}>{label}</a>
      ))}
    </nav>
  );
}


function AutomationReportStats({
  insights,
  proposals,
  runSummary,
  selected,
  successLabel,
}: {
  insights: Record<string, unknown>[];
  proposals: Record<string, unknown>[];
  runSummary: Record<string, unknown>;
  selected: Record<string, unknown>;
  successLabel: string;
}) {
  const lastRunId = textValue(asRecord(selected.last_run).run_id);
  const stats = [
    ["Status", String(selected.status || "idle"), String(selected.window || "current"), automationStatusTone(String(selected.status || ""))],
    ["Next Run", textValue(selected.next_run) || "manual", "schedule", "info"],
    ["Last Run", lastRunId ? automationShortRunId(lastRunId) : "none", "latest", lastRunId ? "info" : "muted"],
    ["Success", successLabel, "terminal runs", successLabel === "n/a" ? "muted" : "ok"],
    ["Run Events", stringify(runSummary.events_total ?? 0)],
    ["Critical", String(insights.filter((insight) => automationInsightTone(String(insight.severity || "")) === "err").length)],
    ["Proposals", String(proposals.length)],
  ];
  return (
    <div className="automation-report-stats">
      {stats.map(([label, value, meta = "", tone = "muted"]) => (
        <div className={`automation-report-stat tone-${tone}`} key={label}>
          <strong>{value}</strong>
          <span>{label}</span>
          {meta ? <small>{meta}</small> : null}
        </div>
      ))}
    </div>
  );
}


function cyclePairLabel(p50: unknown, p90: unknown): string {
  const a = numberValue(p50);
  const b = numberValue(p90);
  if (a === null && b === null) return "—";
  return `${a ?? "—"}/${b ?? "—"}h`;
}


function yieldPercent(value: unknown): string {
  const n = numberValue(value);
  return n === null ? "—" : `${Math.round(n * 100)}%`;
}


function commitsLabel(commits: unknown, perFeature: unknown): string {
  const total = numberValue(commits);
  if (total === null) return "—";
  const ratio = numberValue(perFeature);
  return ratio === null ? String(total) : `${total} (${ratio}/feat)`;
}


function AutomationArchetypeMatrix({ matrix }: { matrix: Record<string, unknown> }) {
  const rows = ["feature", "refactor", "bugfix"]
    .map((key) => ({ key, data: asRecord(matrix[key]) }))
    .filter((row) => Object.keys(row.data).length > 0);
  if (rows.length === 0) return null;
  return (
    <section className="subsection automation-archetype-section">
      <div className="inline-heading">
        <h3>交付按场景</h3>
        <span className="muted">feature / refactor / bugfix</span>
      </div>
      <div className="automation-archetype-grid">
        <div className="automation-archetype-row automation-archetype-head">
          <span>场景</span>
          <span>Features</span>
          <span>Done</span>
          <span>Cycle p50/p90</span>
          <span>FPY</span>
          <span>Commits/feat</span>
        </div>
        {rows.map(({ key, data }) => (
          <div className="automation-archetype-row" key={key}>
            <span className="automation-archetype-label">{key}</span>
            <span>{stringify(data.features ?? 0)}</span>
            <span>{stringify(data.done_tasks ?? 0)}</span>
            <span>{cyclePairLabel(data.cycle_p50_hours, data.cycle_p90_hours)}</span>
            <span>{yieldPercent(data.first_pass_yield)}</span>
            <span>{commitsLabel(data.commits, data.commits_per_feature)}</span>
          </div>
        ))}
      </div>
    </section>
  );
}


function AutomationScorecard({ scorecard }: { scorecard: Record<string, unknown> }) {
  const autonomy = asRecord(scorecard.autonomy);
  const reliability = asRecord(scorecard.reliability);
  const governance = asRecord(scorecard.governance);
  if (!Object.keys(reliability).length && !Object.keys(autonomy).length) return null;
  const interventions = numberValue(autonomy.interventions_total) ?? 0;
  const incidents = numberValue(reliability.incidents_total) ?? 0;
  const critical = numberValue(reliability.critical_total) ?? 0;
  const violations = numberValue(governance.violations_total) ?? 0;
  const stats: [string, string, string, string][] = [
    ["人工介入", String(interventions), "autonomy", interventions > 0 ? "info" : "ok"],
    ["可靠性事故", String(incidents), critical ? `${critical} critical` : "reliability", critical ? "err" : incidents ? "warn" : "ok"],
    ["治理信号", String(violations), "governance", violations ? "warn" : "ok"],
  ];
  return (
    <section className="subsection automation-scorecard-section">
      <div className="inline-heading">
        <h3>横切面</h3>
        <span className="muted">自主性 / 可靠性 / 治理</span>
      </div>
      <div className="automation-report-stats">
        {stats.map(([label, value, meta, tone]) => (
          <div className={`automation-report-stat tone-${tone}`} key={label}>
            <strong>{value}</strong>
            <span>{label}</span>
            <small>{meta}</small>
          </div>
        ))}
      </div>
    </section>
  );
}


function AutomationDecisionPanel({ panel }: { panel: Record<string, unknown> }) {
  const total = numberValue(panel.total) ?? 0;
  if (total <= 0) return null;
  const escalations = asRecord(panel.escalations_awaiting);
  const replan = asRecord(panel.replan_awaiting_owner);
  const proposals = asRecord(panel.proposals_pending);
  const escCount = numberValue(escalations.count) ?? 0;
  const age = numberValue(escalations.oldest_age_hours);
  return (
    <section className={`subsection automation-decision-section tone-${escCount ? "warn" : "info"}`}>
      <div className="inline-heading">
        <h3>决策面</h3>
        <span className="muted">待你拍板 {total}</span>
      </div>
      <div className="automation-report-stats">
        <div className={`automation-report-stat tone-${escCount ? "warn" : "ok"}`}>
          <strong>{escCount}</strong>
          <span>escalation 未接</span>
          {age !== null ? <small>最久 {age}h</small> : null}
        </div>
        <div className="automation-report-stat tone-info">
          <strong>{numberValue(replan.count) ?? 0}</strong>
          <span>replan 待批</span>
        </div>
        <div className="automation-report-stat tone-info">
          <strong>{numberValue(proposals.count) ?? 0}</strong>
          <span>proposal 待审</span>
        </div>
      </div>
    </section>
  );
}


function AutomationOutputs({ outputs }: { outputs: Record<string, unknown>[] }) {
  return (
    <section className="subsection automation-output-section" id="automation-outputs">
      <div className="inline-heading">
        <h3>Outputs</h3>
        <span className="muted">{outputs.length}</span>
      </div>
      {outputs.length === 0 ? (
        <ProjectionEmptyState
          state={{
            title: "No output yet",
            description: "Manual or scheduled automation runs will publish reports here.",
            icon: FileText,
            compact: true,
          }}
        />
      ) : (
        <div className="automation-output-grid">
          {outputs.map((output, index) => (
            <article className="automation-output-card" key={`${String(output.type || "report")}-${index}`}>
              <div className="automation-output-head">
                <span className="badge badge-info">{String(output.type || "report")}</span>
                <span className="muted">{String(output.window || "-")}</span>
              </div>
              <strong>{String(output.summary || "No summary projected.")}</strong>
              <div className="automation-output-metrics">
                {automationOutputMetrics(output).map((metric) => (
                  <span key={metric.label}>
                    <strong>{metric.value}</strong>
                    {metric.label}
                  </span>
                ))}
              </div>
              <ReportPreviewRefs refs={asRecord(output.refs) ?? output} />
            </article>
          ))}
        </div>
      )}
    </section>
  );
}


function ReportPreviewRefs({ refs }: { refs: Record<string, unknown> }) {
  const items = previewItemsFromRefs(refs).slice(0, 8);
  if (!items.length) return null;
  return (
    <div className="agent-ref-chips automation-preview-refs" aria-label="Report preview refs">
      {items.map((item, index) => (
        <span className={`agent-ref-chip profile-${item.profile || "text"}`} key={`${item.kind}-${item.id || item.name}-${index}`}>
          <span>{item.name}</span>
          <small>{item.meta || item.kind}</small>
        </span>
      ))}
    </div>
  );
}


function AutomationRunCounts({ rows }: { rows: Record<string, unknown>[] }) {
  if (rows.length === 0) return null;  // zero-state noise (doc116 §12.2)
  return (
    <details className="subsection automation-run-counts-section">
      <summary className="muted">Run Counts By Day · {rows.length} days</summary>
      {rows.length === 0 ? (
        <ProjectionEmptyState
          state={{
            title: "No run counts yet",
            description: "Run count projections appear after an automation emits run events.",
            icon: CalendarClock,
            compact: true,
          }}
        />
      ) : (
        <div className="automation-day-list">
          {rows.map((row, index) => {
            const rate = automationRateNumber(row.success_rate);
            return (
              <article className="automation-day-card" key={`${String(row.date || "day")}-${index}`}>
                <div className="automation-day-head">
                  <strong>{String(row.date || "-")}</strong>
                  <span className={`badge badge-${rate >= 1 ? "ok" : rate > 0 ? "warn" : "err"}`}>
                    {automationPercent(row.success_rate)}
                  </span>
                </div>
                <div className="automation-success-meter" aria-label="success rate">
                  <span style={{ width: `${Math.max(0, Math.min(100, rate * 100))}%` }} />
                </div>
                <div className="automation-day-metrics">
                  <span><strong>{stringify(row.started ?? 0)}</strong>started</span>
                  <span><strong>{stringify(row.completed ?? 0)}</strong>completed</span>
                  <span><strong>{stringify(row.failed ?? 0)}</strong>failed</span>
                  <span><strong>{stringify(row.skipped ?? 0)}</strong>skipped</span>
                  <span><strong>{stringify(row.events_total ?? 0)}</strong>events</span>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </details>
  );
}


function AutomationRecentRuns({ runs }: { runs: Record<string, unknown>[] }) {
  const recent = [...runs].reverse();
  return (
    <section className="subsection automation-recent-runs-section">
      <div className="inline-heading">
        <h3>Recent Runs</h3>
        <span className="muted">{runs.length} runs</span>
      </div>
      {recent.length === 0 ? (
        <ProjectionEmptyState
          state={{
            title: "No recent runs",
            description: "Manual and scheduled automation executions will appear in this ledger.",
            icon: PlayCircle,
            compact: true,
          }}
        />
      ) : (
        <div className="automation-run-list">
          {recent.map((run, index) => {
            const outputs = Array.isArray(run.outputs)
              ? run.outputs.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
              : [];
            const summary = outputs.find((output) => String(output.summary || "").trim())?.summary;
            const sourceEvents = Array.isArray(run.source_events) ? run.source_events.length : 0;
            return (
              <article className="automation-run-card" key={String(run.run_id || index)}>
                <div className="automation-run-main">
                  <span className={`badge badge-${automationStatusTone(String(run.status || ""))}`}>
                    {String(run.status || "unknown")}
                  </span>
                  <strong>{automationShortRunId(String(run.run_id || "run"))}</strong>
                  <span className="muted">{String(run.trigger || "manual")}</span>
                </div>
                <p>{String(summary || run.failure_reason || "No output summary.")}</p>
                <div className="automation-run-meta">
                  <span className="mono">{automationShortId(String(run.started_event_id || ""))}</span>
                  {String(run.completed_event_id || "").trim() ? (
                    <span className="mono">{automationShortId(String(run.completed_event_id || ""))}</span>
                  ) : null}
                  {sourceEvents ? <span>{sourceEvents} refs</span> : null}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}


function AutomationInsights({ insights }: { insights: Record<string, unknown>[] }) {
  if (!insights.length) {
    return (
      <ProjectionEmptyState
        state={{
          title: "No projected insights",
          description: "Automation insights will surface here when reports detect warnings, proposals, or useful summaries.",
          icon: Bell,
          compact: true,
        }}
      />
    );
  }
  return (
    <div className="automation-insight-grid">
      {insights.map((insight, index) => {
        const severity = String(insight.severity || "info");
        const sourceEvents = Array.isArray(insight.source_events)
          ? insight.source_events.filter((item) => String(item || "").trim()).length
          : 0;
        const taskIds = Array.isArray(insight.task_ids)
          ? insight.task_ids.filter((item) => String(item || "").trim()).length
          : 0;
        return (
          <article className={`automation-insight-card severity-${automationInsightTone(severity)}`} key={String(insight.id || index)}>
            <div className="automation-insight-head">
              <span className={`badge badge-${automationInsightTone(severity)}`}>
                {severity}
              </span>
              <span className="muted">{String(insight.category || "summary")}</span>
            </div>
            <strong>{String(insight.title || "Insight")}</strong>
            <p>{String(insight.summary || "-")}</p>
            <div className="automation-insight-meta">
              {insight.metric !== null && insight.metric !== undefined ? (
                <span className="mono">{stringify(insight.metric)}</span>
              ) : null}
              {sourceEvents ? <span>{sourceEvents} refs</span> : null}
              {taskIds ? <span>{taskIds} tasks</span> : null}
            </div>
            {String(insight.suggested_action || "").trim() ? (
              <span className="muted">{String(insight.suggested_action)}</span>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}


function automationInsightTone(severity: string): "ok" | "warn" | "err" | "info" {
  const normalized = severity.toLowerCase();
  if (normalized === "ok" || normalized === "success") return "ok";
  if (normalized === "critical" || normalized === "error" || normalized === "err") return "err";
  if (normalized === "warn" || normalized === "warning") return "warn";
  return "info";
}


function automationGlanceTone(insights: Record<string, unknown>[]): "ok" | "warn" | "err" | "info" {
  if (insights.some((insight) => automationInsightTone(String(insight.severity || "")) === "err")) return "err";
  if (insights.some((insight) => automationInsightTone(String(insight.severity || "")) === "warn")) return "warn";
  if (insights.length > 0) return "ok";
  return "info";
}


function automationSeverityRank(severity: string): number {
  const tone = automationInsightTone(severity);
  if (tone === "err") return 3;
  if (tone === "warn") return 2;
  if (tone === "info") return 1;
  return 0;
}


function automationTopAttention(insights: Record<string, unknown>[]): Record<string, unknown> | null {
  const ranked = [...insights]
    .filter((insight) => automationSeverityRank(String(insight.severity || "")) >= 2)
    .sort((a, b) => automationSeverityRank(String(b.severity || "")) - automationSeverityRank(String(a.severity || "")));
  return ranked[0] ?? null;
}


function automationSuggestedActions(insights: Record<string, unknown>[]): string[] {
  const seen = new Set<string>();
  const actions: string[] = [];
  for (const insight of insights) {
    const action = String(insight.suggested_action || "").trim();
    if (!action || seen.has(action)) continue;
    seen.add(action);
    actions.push(action.endsWith(".") ? action : `${action}.`);
    if (actions.length >= 2) break;
  }
  return actions;
}


function automationPercent(value: unknown): string {
  const numeric = automationRateNumber(value);
  if (!Number.isFinite(numeric)) return "n/a";
  return `${Math.round(numeric * 100)}%`;
}


function automationSuccessLabel(runSummary: Record<string, unknown>): string {
  const explicitRate = numberValue(runSummary.success_rate);
  const terminal = automationCount(runSummary.terminal_total);
  const events = automationCount(runSummary.events_total);
  const completed = automationCount(runSummary.completed);
  const failed = automationCount(runSummary.failed);
  const denominator = completed + failed || terminal;
  if (explicitRate !== null && (terminal > 0 || events > 0 || denominator > 0)) {
    return `${Math.round(explicitRate * 100)}%`;
  }
  if (denominator > 0) {
    return `${Math.round((completed / denominator) * 100)}%`;
  }
  return "n/a";
}


function automationRateNumber(value: unknown): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}


function automationTriggerLabels(value: unknown): string[] {
  const raw = textValue(value).trim();
  if (!raw) return ["manual"];
  const labels = raw.split(/[|,]/).map((item) => item.trim()).filter(Boolean);
  return labels.length ? labels : [raw];
}


function automationCount(value: unknown): number {
  if (Array.isArray(value)) return value.length;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}


function automationOutputMetrics(output: Record<string, unknown>): { label: string; value: string }[] {
  const taskCounts = asRecord(output.task_counts);
  return [
    { label: "active", value: String(automationCount(output.active_tasks) || automationCount(taskCounts.backlog)) },
    { label: "blocked", value: String(automationCount(output.blocked_tasks) || automationCount(taskCounts.blocked)) },
    { label: "failed refs", value: String(automationCount(output.failed_events) || automationCount(output.failure_events)) },
    { label: "proposals", value: String(automationCount(output.proposals)) },
  ];
}

