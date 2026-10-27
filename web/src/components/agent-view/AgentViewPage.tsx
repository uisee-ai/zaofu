import { AgentFleetTable, WorkerDetailsDialog } from "./AgentFleetTable";
// AgentViewPage + exclusive closure, extracted verbatim from App.tsx (P1 split).
import { getAgents } from "../../api/client";
import type { AgentSummary, ChannelSummary, ExecutionPatternProjection, Task, RecentEvent } from "../../api/types";
import { formatTokens } from "../../lib/format";
import { buildAgentAttentionRows, buildFleetMetrics, buildRoleFleetRows, contextPercent, isBackendWorker, needsAttention } from "../../app/cockpitModel";
import { buildProjectCostPresentation } from "../../app/costPrecision";
import { useAgentMonitoring } from "./useAgentMonitoring";
import { useEffect, useMemo, useState } from "react";
import { KeyValuePanel, RuntimeDetailSection, RuntimeSummaryCard, TablePage, asRecord, formatUsd, needsOperatorAttention, textValue } from "../../app/shared";
import { fetchTerminalSessions } from "../terminal/api";
import type { TerminalSessionsPage } from "../terminal/types";
import { buildTerminalUsageRows } from "./terminalUsageRows";

export function AgentViewPage({
  actionReady,
  recentEvents = [],
  liveState = "live",
  assignmentRoutes,
  channels,
  executionPatterns,
  onAddAgentToChannel,
  onAction,
  onSelectTask,
  projectId,
  providerCapabilities,
  recovery,
}: {
  actionReady: boolean;
  recentEvents?: RecentEvent[];
  liveState?: string;
  assignmentRoutes: Record<string, unknown> | null;
  channels: ChannelSummary[];
  executionPatterns: ExecutionPatternProjection | null;
  onAddAgentToChannel: (agent: AgentSummary) => void;
  onAction: (action: string, payload: Record<string, unknown>) => void;
  onSelectTask: (taskId: string) => void;
  projectId: string;
  providerCapabilities: Record<string, unknown> | null;
  recovery: Record<string, unknown> | null;
}) {
  const monitoring = useAgentMonitoring(projectId, recentEvents, liveState);
  const [terminalProjection, setTerminalProjection] = useState<{
    page: TerminalSessionsPage | null;
    projectId: string;
    status: "idle" | "loading" | "active" | "failed";
  }>({ page: null, projectId: "", status: "idle" });

  useEffect(() => {
    if (!projectId) {
      setTerminalProjection({ page: null, projectId: "", status: "idle" });
      return;
    }
    let cancelled = false;
    let inFlight = false;
    const load = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const page = await fetchTerminalSessions(projectId);
        if (!cancelled) {
          setTerminalProjection({ page, projectId, status: "active" });
        }
      } catch {
        if (!cancelled) {
          setTerminalProjection((current) => ({
            page: current.projectId === projectId ? current.page : null,
            projectId,
            status: "failed",
          }));
        }
      } finally {
        inFlight = false;
      }
    };
    setTerminalProjection((current) => ({
      page: current.projectId === projectId ? current.page : null,
      projectId,
      status: "loading",
    }));
    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 10_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [projectId]);

  const effectiveAgents = monitoring.data?.agents ?? [];
  const effectiveAgentCockpit = monitoring.data?.agent_cockpit ?? null;
  const effectiveAgentLive = monitoring.data?.agent_live ?? null;
  const metricsReady = Boolean(monitoring.data);
  const fleetStats = monitoring.data?.fleet_stats ?? null;
  const metricsSnapshot = monitoring.data?.metrics_snapshot ?? null;
  const projectionSource = monitoring.error ? "Refresh failed" : monitoring.loading
    ? metricsReady ? "Refreshing" : "Loading metrics" : "Live metrics";
  const workers = useMemo(
    () => effectiveAgents.filter(isBackendWorker),
    [effectiveAgents],
  );
  const attentionWorkers = useMemo(
    () => workers.filter((agent) => needsAttention(agent.attention_state)),
    [workers],
  );
  const [selectedId, setSelectedId] = useState("");
  const effectiveCost = monitoring.data?.cost ?? null;
  const fleetMetrics = buildFleetMetrics(effectiveAgents, effectiveAgentCockpit, effectiveCost);
  const projectCost = buildProjectCostPresentation(effectiveCost);
  const displayedTokens = projectCost.hasUsage
    ? projectCost.totalTokens
    : fleetMetrics.totalInputTokens + fleetMetrics.totalOutputTokens;
  const displayedCost = projectCost.hasUsage ? projectCost.totalUsd : fleetMetrics.totalCostUsd;
  const agentsUnavailable = !metricsReady && !monitoring.loading && Boolean(monitoring.error);
  const agentsLoading = !metricsReady && !agentsUnavailable;
  const agentsUnknown = agentsLoading || agentsUnavailable;
  const agentAttentionRows = buildAgentAttentionRows(effectiveAgents, effectiveAgentCockpit, recovery);
  const roleFleetRows = buildRoleFleetRows(effectiveAgents, effectiveCost);
  const terminalPage = terminalProjection.projectId === projectId
    ? terminalProjection.page
    : null;
  const terminalUsageRows = useMemo(
    () => buildTerminalUsageRows(terminalPage?.sessions ?? []),
    [terminalPage?.sessions],
  );
  const activeTerminalCount = terminalUsageRows.filter((row) => row.state === "active").length;

  useEffect(() => {
    // Drawer semantics: nothing selected by default; only clear a selection
    // that no longer resolves to a live worker row.
    if (selectedId && !workers.some((worker) => worker.instance_id === selectedId)) {
      setSelectedId("");
    }
  }, [selectedId, workers]);

  const groups = useMemo(() => {
    const grouped = new Map<string, AgentSummary[]>();
    for (const worker of workers) {
      const role = worker.parent_role || worker.role_type || "unknown";
      grouped.set(role, [...(grouped.get(role) ?? []), worker]);
    }
    return [...grouped.entries()].sort(([left], [right]) => left.localeCompare(right));
  }, [workers]);
  const rosterRows = useMemo(() => {
    const rows: Record<string, unknown>[] = [];
    for (const [role, roleWorkers] of groups) {
      const backends = [...new Set(roleWorkers.map((worker) => worker.backend).filter(Boolean))];
      const activeCount = roleWorkers.filter((worker) => textValue(worker.lifecycle_state || worker.runtime_state || worker.state) === "running").length;
      rows.push({
        agent: role,
        role,
        backend: backends.join(", ") || "-",
        model: [...new Set(roleWorkers.map((worker) => worker.model).filter(Boolean))].join(", ") || "-",
        last_run: roleWorkers.map((worker) => worker.last_heartbeat).filter(Boolean).sort().slice(-1)[0] || "-",
        status: activeCount ? `${activeCount}/${roleWorkers.length} running` : `${roleWorkers.length} projected`,
        tools: [...new Set(roleWorkers.flatMap((worker) => worker.skills ?? []))].slice(0, 4).join(", ") || "-",
      });
      for (const worker of roleWorkers) {
        rows.push({
          agent: `  ${worker.instance_id}`,
          role: worker.role_type || role,
          backend: worker.backend || "-",
          model: worker.model || "-",
          last_run: worker.last_heartbeat || worker.spawned_at || "-",
          status: worker.stale ? `last known: ${worker.lifecycle_state || worker.state || "-"}` : (worker.lifecycle_state || worker.runtime_state || worker.state || "-"),
          tools: (worker.skills ?? []).slice(0, 4).join(", ") || "-",
        });
      }
    }
    return rows;
  }, [groups]);

  const [detail, setDetail] = useState<{ projectId: string; agent: AgentSummary } | null>(null);
  useEffect(() => {
    if (!selectedId || !projectId) return;
    let cancelled = false;
    void getAgents(projectId).then(rows => {
      const agent = rows.find(row => row.instance_id === selectedId);
      if (!cancelled && agent) setDetail({ projectId, agent });
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [projectId, selectedId]);
  const selectedSummary = workers.find((worker) => worker.instance_id === selectedId) ?? null;
  const details = detail?.projectId === projectId && detail.agent.instance_id === selectedId ? detail.agent : null;
  const selected = selectedSummary && details ? { ...selectedSummary,
    cwd: details.cwd, project_path: details.project_path, workdir: details.workdir,
    branch: details.branch, branch_or_ref: details.branch_or_ref, debug: details.debug,
  } : selectedSummary;
  const selectedTaskId = selected?.task_id || selected?.active_task || "";
  const selectedActions = new Set(selected?.allowed_actions ?? []);
  const workerActionReady = (action: string) => (
    Boolean(selected?.instance_id) && actionReady && selectedActions.has(action)
  );
  const runWorkerAction = (action: "reply" | "respawn" | "drain") => {
    if (!selected?.instance_id) return;
    if (action === "reply") {
      const message = window.prompt(`Reply to ${selected.instance_id}`);
      if (!message?.trim()) return;
      onAction("worker-reply", {
        instance_id: selected.instance_id,
        task_id: selectedTaskId || undefined,
        message: message.trim(),
      });
      return;
    }
    onAction(`worker-${action}`, {
      instance_id: selected.instance_id,
      task_id: selectedTaskId || undefined,
      reason: "agent_view_operator_action",
    });
  };
  const selectedRows = selected ? [
    { key: "instance_id", value: selected.instance_id },
    { key: "parent_role", value: selected.parent_role || selected.role_type },
    { key: "origin", value: selected.origin || "static" },
    { key: "backend", value: selected.backend || "-" },
    { key: "lifecycle", value: selected.lifecycle_state || selected.runtime_state || selected.state },
    { key: "attention", value: selected.attention_state || "idle" },
    { key: "task", value: selectedTaskId || "-" },
    { key: "cwd", value: selected.cwd || selected.project_path || selected.workdir || "-" },
    { key: "branch", value: selected.branch || selected.branch_or_ref || "-" },
    { key: "context", value: selected.context_usage_ratio == null ? "-" : `${Math.round(selected.context_usage_ratio * 100)}%` },
    { key: "last_event", value: selected.last_event_type || "-" },
    { key: "actions", value: (selected.allowed_actions ?? []).join(", ") || "-" },
  ] : [];
  const zeroCost = { usd: 0, input_tokens: 0, output_tokens: 0, entries: 0 };
  const ledgerEntries = Object.entries(effectiveCost?.per_role ?? {});
  const hasLedgerUsage = ledgerEntries.some(([, entry]) => (
    (entry.entries ?? 0) > 0
    || (entry.input_tokens ?? 0) > 0
    || (entry.output_tokens ?? 0) > 0
    || (entry.usd ?? 0) > 0
  ));
  const roleMeta = new Map<string, {
    attention: number;
    backends: Set<string>;
    maxContext: number | null;
    workers: number;
  }>();
  const workerCostByRole = new Map<string, typeof zeroCost>();
  for (const worker of workers) {
    const role = worker.parent_role || worker.role_type || worker.instance_id || "unknown";
    const meta = roleMeta.get(role) ?? {
      attention: 0,
      backends: new Set<string>(),
      maxContext: null,
      workers: 0,
    };
    meta.workers += 1;
    if (worker.backend) meta.backends.add(worker.backend);
    if (needsOperatorAttention(worker.attention_state)) meta.attention += 1;
    if (typeof worker.context_usage_ratio === "number") {
      meta.maxContext = Math.max(meta.maxContext ?? 0, worker.context_usage_ratio);
    }
    roleMeta.set(role, meta);

    const current = workerCostByRole.get(role) ?? zeroCost;
    workerCostByRole.set(role, {
      usd: current.usd + (worker.cost?.usd ?? 0),
      input_tokens: current.input_tokens + (worker.cost?.input_tokens ?? 0),
      output_tokens: current.output_tokens + (worker.cost?.output_tokens ?? 0),
      entries: current.entries + (worker.cost?.entries ?? 0),
    });
  }
  const roleNames = [...new Set([
    ...roleMeta.keys(),
    ...ledgerEntries.map(([role]) => role),
  ])].sort((left, right) => left.localeCompare(right));
  const roleUsageRowsRaw = roleNames.map((role) => {
    const usage = hasLedgerUsage
      ? (effectiveCost?.per_role[role] ?? workerCostByRole.get(role) ?? zeroCost)
      : (workerCostByRole.get(role) ?? effectiveCost?.per_role[role] ?? zeroCost);
    const meta = roleMeta.get(role);
    const totalTokens = (usage.input_tokens ?? 0) + (usage.output_tokens ?? 0);
    return {
      role,
      workers: meta?.workers ?? 0,
      backend: [...(meta?.backends ?? new Set<string>())].join(", ") || "-",
      max_context: meta?.maxContext == null ? "unknown" : `${Math.round(meta.maxContext * 100)}%`,
      attention: meta?.attention ?? 0,
      entries: usage.entries ?? 0,
      input_tokens: usage.input_tokens ?? 0,
      output_tokens: usage.output_tokens ?? 0,
      total_tokens: totalTokens,
      usd: usage.usd ?? 0,
    };
  });
  const roleTokenRows = roleUsageRowsRaw.map((row) => ({
    role: row.role,
    workers: row.workers,
    backend: row.backend,
    max_context: row.max_context,
    total_tokens: formatTokens(row.total_tokens),
    input_tokens: formatTokens(row.input_tokens),
    output_tokens: formatTokens(row.output_tokens),
    usd: formatUsd(row.usd),
    attention: row.attention,
  }));
  const contextRows = workers.map((worker) => ({
    instance_id: worker.instance_id,
    role: worker.parent_role || worker.role_type || "-",
    backend: worker.backend || "-",
    task_id: worker.task_id || worker.active_task || "-",
    context: worker.context_usage_ratio == null ? "unknown" : `${Math.round(worker.context_usage_ratio * 100)}%`,
    context_risk: worker.context_usage_ratio == null
      ? "unknown"
      : worker.context_usage_ratio >= 0.9
        ? "high"
        : worker.context_usage_ratio >= 0.75
          ? "watch"
          : "normal",
    input_tokens: worker.cost?.input_tokens ?? 0,
    output_tokens: worker.cost?.output_tokens ?? 0,
    usd: formatUsd(worker.cost?.usd),
    attention: worker.attention_state || "idle",
  }));
  const usageContextRows = contextRows.length ? contextRows : roleTokenRows;
  const liveAssignmentRows = (
    Array.isArray(effectiveAgentLive?.tasks) ? effectiveAgentLive.tasks : []
  ).map((item) => {
    const row = asRecord(item);
    const liveWorkers = Array.isArray(row.workers) ? row.workers : [];
    const activeWorkers = Array.isArray(row.active_workers) ? row.active_workers : [];
    const queuedWorkers = Array.isArray(row.queued_workers) ? row.queued_workers : [];
    const routeEvents = Array.isArray(row.route_events) ? row.route_events : [];
    return {
      task_id: String(row.task_id || "_project"),
      workers: liveWorkers.length,
      active: activeWorkers.length,
      queued: queuedWorkers.length,
      route_events: routeEvents.length,
    };
  });
  const assignmentRouteRows = (
    Array.isArray(assignmentRoutes?.routes) ? assignmentRoutes.routes : []
  ).map((item) => {
    const row = asRecord(item);
    return {
      stage: String(row.stage || "observed"),
      task_id: String(row.task_id || "-"),
      assignee_type: String(row.assignee_type || "-"),
      assignee_id: String(row.assignee_id || "-"),
      channel_id: String(row.channel_id || "-"),
      pattern_id: String(row.pattern_id || "-"),
      dispatches: Boolean(row.dispatches),
      execution_started: Boolean(row.execution_started),
    };
  });
  const cockpitSummary = asRecord(effectiveAgentCockpit?.summary);
  const cockpitRows = (
    Array.isArray(effectiveAgentCockpit?.workers) ? effectiveAgentCockpit.workers : []
  ).map((item) => {
    const row = asRecord(item);
    const reasons = Array.isArray(row.reasons) ? row.reasons : [];
    const actions = Array.isArray(row.next_actions) ? row.next_actions : [];
    const ratio = typeof row.context_usage_ratio === "number" ? row.context_usage_ratio : null;
    return {
      instance_id: String(row.instance_id || "-"),
      status: String(row.status || "unknown"),
      role: String(row.role || "-"),
      task_id: String(row.task_id || "-"),
      heartbeat_age_sec: row.heartbeat_age_sec ?? "-",
      context: ratio == null ? "unknown" : `${Math.round(ratio * 100)}%`,
      signals: Number(row.signal_count ?? 0),
      reasons: reasons.slice(-2).map(String).join(" | ") || "-",
      next_actions: actions.slice(0, 3).map(String).join(", ") || "-",
    };
  });
  const recoveryRows = (
    Array.isArray(recovery?.runs) ? recovery.runs : []
  ).map((item) => {
    const row = asRecord(item);
    return {
      run_id: String(row.run_id || "-"),
      source: String(row.source || "-"),
      status: String(row.status || "unknown"),
      task_id: String(row.task_id || "-"),
      instance_id: String(row.instance_id || "-"),
      steps: Number(row.step_count ?? 0),
      failed_steps: Number(row.failed_steps ?? 0),
      last_event_at: String(row.last_event_at || "-"),
    };
  });
  const recoverySuggestionRows = (
    Array.isArray(recovery?.suggestions) ? recovery.suggestions : []
  ).map((item) => {
    const row = asRecord(item);
    return {
      type: String(row.suggestion_type || "-"),
      recommended_recovery: String(row.recommended_recovery || "-"),
      task_id: String(row.task_id || "-"),
      instance_id: String(row.instance_id || "-"),
      trigger_event_id: String(row.trigger_event_id || "-"),
      reason: String(row.reason || "-"),
    };
  });
  const providerHealthRows = (
    Array.isArray(providerCapabilities?.providers) ? providerCapabilities.providers : []
  )
    .map((item) => asRecord(item))
    .filter((row) => {
      const availability = String(row.availability || (row.available === false ? "unavailable" : "")).toLowerCase();
      return availability.includes("unavailable") || availability.includes("degraded");
    })
    .map((row) => ({
      backend: String(row.backend || row.provider || "-"),
      availability: String(row.availability || (row.available === false ? "unavailable" : "degraded")),
      surface: String(row.surface || "-"),
    }));

  return (
    <>
      <div className="section-heading">
        <div>
          <h2>Agents</h2>
          <span className="muted">
            {agentsUnavailable ? "Agent status unavailable" : agentsLoading ? "Loading agents…" : `${workers.length} agents`}
            {terminalPage?.enabled ? ` · ${activeTerminalCount} terminal tabs` : ""}

          </span>
        </div>
        <span className="muted" role="status">
          {projectionSource}
          {monitoring.data?.generated_at && <time dateTime={monitoring.data.generated_at}>
            {` · ${new Date(monitoring.data.generated_at).toLocaleTimeString()}`}
          </time>}
        </span>
      </div>

      {monitoring.error && <p role="alert">Metrics refresh failed. {metricsReady ? "Showing the previous reading." : "Retrying."}</p>}
      <div className="agent-fleet-summary-grid" aria-busy={monitoring.loading}
        data-metrics-ready={metricsReady && !monitoring.error}
        data-refreshing={monitoring.loading && metricsReady}
        data-source-seq={monitoring.data?.source_seq}>
        <RuntimeSummaryCard
          label="Workers"
          value={agentsUnknown ? "—" : fleetMetrics.backendWorkers}
          meta={agentsUnknown ? "—" : `${fleetMetrics.controlAgents} control / ${fleetMetrics.operatorAgents} operator`}
          tone={fleetMetrics.backendWorkers ? "info" : "muted"}
        />
        <RuntimeSummaryCard
          label="Health"
          value={agentsUnknown ? "—" : `${fleetMetrics.stuck} stuck`}
          meta={agentsUnknown ? "—" : typeof metricsSnapshot?.mtts === "number" && metricsSnapshot.mtts > 0
            ? `MTTS ${metricsSnapshot.mtts.toFixed(1)}h · recovery ${Math.round((metricsSnapshot.stuck_recovery_rate ?? 0) * 100)}% · ${fleetMetrics.silent} silent`
            : `${fleetMetrics.silent} silent / ${fleetMetrics.drift} drift`}
          tone={fleetMetrics.stuck || fleetMetrics.silent ? "err" : fleetMetrics.drift ? "warn" : "ok"}
        />
        <RuntimeSummaryCard
          label="Context"
          value={agentsUnknown ? "—" : contextPercent(fleetMetrics.maxContext)}
          meta={agentsUnknown ? "—" : `${fleetMetrics.contextWarn} warning`}
          tone={fleetMetrics.contextWarn ? "warn" : "ok"}
        />
        <RuntimeSummaryCard
          label="Tokens"
          value={agentsUnknown && !projectCost.hasUsage ? "—" : formatTokens(displayedTokens)}
          meta={typeof metricsSnapshot?.cost_per_task === "number" && metricsSnapshot.cost_per_task > 0
            ? `${formatUsd(displayedCost)} · $${metricsSnapshot.cost_per_task.toFixed(2)}/task${projectCost.precisionLabel ? ` · ${projectCost.precisionLabel}` : ""}`
            : `${formatUsd(displayedCost)}${projectCost.precisionLabel ? ` · ${projectCost.precisionLabel}` : ""}`}
          tone={displayedTokens || displayedCost
            ? projectCost.hasUsage ? projectCost.precisionTone : "info"
            : "muted"}
        />
        <RuntimeSummaryCard
          label="Providers"
          value={fleetMetrics.providerSummary}
          meta={agentsUnavailable ? "Unavailable" : agentsLoading ? "Loading…" : "Live projection"}
          tone={fleetMetrics.providerSummary === "-" ? "muted" : "info"}
        />
      </div>

      {!agentsUnknown && <section className="subsection agent-attention-cockpit">
        <div className="inline-heading">
          <h3 className="section-title">Runtime signals</h3>
          <span className="muted">{agentAttentionRows.length} signals</span>
        </div>
        {agentAttentionRows.length ? (
          <div className="compact-list">
            {agentAttentionRows.slice(0, 8).map((row, index) => (
              <button
                className="inline-row"
                key={`${row.source_projection}:${row.target}:${index}`}
                type="button"
                onClick={() => row.domain === "agent" && setSelectedId(row.target)}
              >
                <span className={`badge badge-${row.severity} agent-attention-severity`}>{row.severity}</span>
                <span className="mono agent-attention-target">{row.target}</span>
                <span className="agent-attention-reason">{row.reason}</span>
                <span className="muted agent-attention-action">{row.recommended_action}</span>
                <span className="muted mono agent-attention-evidence">{row.evidence}</span>
              </button>
            ))}
          </div>
        ) : (
          <p className="empty-text">
            {!metricsReady ? "Loading attention…" : fleetMetrics.stale
              ? "Historical attention hidden — project runtime is archived/stopped."
              : "No additional runtime signals reported."}
          </p>
        )}
      </section>}

      {terminalPage?.enabled || terminalUsageRows.length ? (
        <section data-testid="interactive-terminals">
          <TablePage
            title="Interactive Terminals"
            rows={terminalUsageRows.map((row) => ({
              tab: row.tab,
              provider: row.provider,
              state: row.state,
              model: row.model,
              context: row.contextUsageRatio == null
                ? "—"
                : `${Math.round(row.contextUsageRatio * 100)}%`,
              tokens: row.totalTokens == null ? "—" : formatTokens(row.totalTokens),
              cost: row.costUsd == null ? "—" : formatUsd(row.costUsd),
              precision: row.precision,
            }))}
            embedded
          />
        </section>
      ) : null}

      {workers.length ? (
        <AgentFleetTable workers={workers} onSelect={setSelectedId} onOpenTask={onSelectTask} />
      ) : (
        <section className="subsection">
          <div className="inline-heading">
            <h3 className="section-title">Role Fleet</h3>
            <span className="muted">{agentsUnknown ? "—" : "0 workers"}</span>
          </div>
          <p className="empty-text" role="status">{agentsUnavailable ? "Could not load agents. Try again." : agentsLoading ? "Loading agents…" : "No agents are running in this project."}</p>
          {agentsUnavailable && <button className="icon-button" type="button" onClick={monitoring.retry}>Retry agents</button>}
        </section>
      )}

      {providerHealthRows.length ? (
        <TablePage title="Provider Health Attention" rows={providerHealthRows} embedded />
      ) : null}
      {/* Stuck Cockpit deleted: same signal source as Attention Queue (counts in
          the Health card, detail rows in the queue) — the last duplicate surface. */}
      <RuntimeDetailSection
        title="Advanced"
        meta={`${assignmentRouteRows.length} routes / ${recoverySuggestionRows.length} recovery suggestions`}
        defaultOpen={false}
      >
        <TablePage title="Worker efficiency" embedded rows={workers.map((worker) => {
          const efficiency = (fleetStats?.role_efficiency ?? []).find((row) => row.role === (worker.parent_role || worker.role_type || worker.instance_id));
          return { instance: worker.instance_id, usd: formatUsd(worker.cost?.usd), done_7d: efficiency?.done ?? 0, rework: efficiency?.rework ?? 0, respawn: efficiency?.respawn ?? 0, attention: worker.attention_state || "idle" };
        })} />
        <TablePage
          title="Assignment Routes"
          rows={assignmentRouteRows}
          embedded
          onOpen={(row) => {
            const taskId = textValue(row.task_id);
            if (taskId && taskId !== "-") onSelectTask(taskId);
          }}
        />
        {recoverySuggestionRows.length ? (
          <TablePage title="Recovery Suggestions" rows={recoverySuggestionRows} embedded />
        ) : null}
      </RuntimeDetailSection>

      <div className="agent-view-summary">

        {/* Second attention block: only surfaces when there is something to act on
            (the Attention Queue at the top already covers the zero state). */}
        {attentionWorkers.length > 0 && (
        <section className="subsection">
          <div className="inline-heading">
            <h3 className="section-title">Attention Needed</h3>
            <span className="muted">{metricsReady ? attentionWorkers.length : "…"} workers</span>
          </div>
          {attentionWorkers.length === 0 ? (
            <p className="empty-text">{metricsReady ? "No worker attention required." : "Loading attention…"}</p>
          ) : (
            <div className="compact-list">
              {attentionWorkers.map((worker) => (
                <button
                  className="inline-row"
                  key={worker.instance_id}
                  type="button"
                  onClick={() => setSelectedId(worker.instance_id)}
                >
                  <span className="mono">{worker.instance_id}</span>
                  <span>{worker.attention_state || "attention"}</span>
                  <span className="muted mono">{worker.task_id || worker.active_task || "-"}</span>
                </button>
              ))}
            </div>
          )}
        </section>
        )}

      </div>

      {selected && (
      <WorkerDetailsDialog onClose={() => setSelectedId("")}>
        <section className="subsection agent-cockpit-panel">
          <div className="inline-heading">
            <h3 className="section-title">Selected Worker</h3>
            <span className="muted">{selected.instance_id}</span>
            <button className="icon-button" aria-label="Close worker details" type="button" onClick={() => setSelectedId("")}>
              Close
            </button>
          </div>
          {selected ? (
            <>
              <KeyValuePanel title="Runtime" rows={selectedRows} />
              <div className="agent-cockpit-grid">
                {/* Controls only make sense against a live runtime; on
                    stale/archived workers they are dead buttons (F0-B ghosts). */}
                {!selected.stale && (
                <div className="subsection compact-subsection">
                  <div className="inline-heading">
                    <h3 className="section-title">Worker Controls</h3>
                    <span className="muted">token gated</span>
                  </div>
                  <div className="action-row">
                    <button
                      className="icon-button"
                      disabled={!workerActionReady("reply")}
                      type="button"
                      onClick={() => runWorkerAction("reply")}
                    >
                      Reply
                    </button>
                    <button
                      className="icon-button"
                      disabled={!workerActionReady("respawn")}
                      type="button"
                      onClick={() => runWorkerAction("respawn")}
                    >
                      Respawn
                    </button>
                    <button
                      className="icon-button"
                      disabled={!workerActionReady("drain")}
                      type="button"
                      onClick={() => runWorkerAction("drain")}
                    >
                      Drain
                    </button>
                    <button
                      className="icon-button"
                      type="button"
                      onClick={() => selected ? onAddAgentToChannel(selected) : undefined}
                    >
                      Add to Channel
                    </button>
                  </div>
                </div>
                )}
                <div className="subsection compact-subsection">
                  <div className="inline-heading">
                    <h3 className="section-title">Task / Evidence</h3>
                    <span className="muted">gate-owned</span>
                  </div>
                  {selectedTaskId ? (
                    <button className="icon-button mono" type="button" onClick={() => onSelectTask(selectedTaskId)}>
                      Open Task {selectedTaskId}
                    </button>
                  ) : (
                    <p className="empty-text">No active task.</p>
                  )}
                  <p className="muted mono">{selected.worktree_path || selected.project_path || selected.workdir || ""}</p>
                </div>
              </div>
            </>
          ) : (
            <p className="empty-text">{metricsReady ? "No backend worker projection data." : "Loading workers…"}</p>
          )}
        </section>
      </WorkerDetailsDialog>
      )}
    </>
  );
}
