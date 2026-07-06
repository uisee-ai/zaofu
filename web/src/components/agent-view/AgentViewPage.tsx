// AgentViewPage + exclusive closure, extracted verbatim from App.tsx (P1 split).
import { getAgentCockpit, getAgentLive, getAgents } from "../../api/client";
import type { AgentSummary, ChannelSummary, CostSummary, ExecutionPatternProjection, FleetStats, MetricsSnapshotProjection, Task } from "../../api/types";
import { formatTokens } from "../../lib/format";
import { buildAgentAttentionRows, buildFleetMetrics, buildRoleFleetRows, contextPercent, isBackendWorker, needsAttention } from "../../app/cockpitModel";
import { useEffect, useMemo, useState } from "react";
import { KeyValuePanel, RuntimeDetailSection, RuntimeSummaryCard, TablePage, asRecord, formatUsd, needsOperatorAttention, textValue } from "../../app/shared";

export function AgentViewPage({
  actionReady,
  agentCockpit,
  agentLive,
  agents,
  assignmentRoutes,
  channels,
  cost,
  executionPatterns,
  fleetStats,
  metricsSnapshot,
  onAddAgentToChannel,
  onAction,
  onSelectTask,
  projectId,
  providerCapabilities,
  recovery,
}: {
  actionReady: boolean;
  agentCockpit: Record<string, unknown> | null;
  agentLive: Record<string, unknown> | null;
  agents: AgentSummary[];
  assignmentRoutes: Record<string, unknown> | null;
  channels: ChannelSummary[];
  cost: CostSummary | null;
  executionPatterns: ExecutionPatternProjection | null;
  fleetStats: FleetStats | null;
  metricsSnapshot: MetricsSnapshotProjection | null;
  onAddAgentToChannel: (agent: AgentSummary) => void;
  onAction: (action: string, payload: Record<string, unknown>) => void;
  onSelectTask: (taskId: string) => void;
  projectId: string;
  providerCapabilities: Record<string, unknown> | null;
  recovery: Record<string, unknown> | null;
}) {
  const [fallback, setFallback] = useState<{
    agentCockpit: Record<string, unknown> | null;
    agentLive: Record<string, unknown> | null;
    agents: AgentSummary[];
    status: "idle" | "loading" | "active" | "failed";
  }>({ agentCockpit: null, agentLive: null, agents: [], status: "idle" });

  useEffect(() => {
    if (!projectId) {
      setFallback({ agentCockpit: null, agentLive: null, agents: [], status: "idle" });
      return;
    }
    if (agents.length > 0 && agentCockpit && agentLive) return;
    let cancelled = false;
    let cockpitTimer: number | undefined;
    setFallback({
      agentCockpit: agentCockpit ?? null,
      agentLive: agentLive ?? null,
      agents: agents.length ? agents : [],
      status: "loading",
    });
    Promise.allSettled([
      agents.length ? Promise.resolve(agents) : getAgents(projectId),
      agentLive ? Promise.resolve(agentLive) : getAgentLive(projectId),
    ]).then(([agentsResult, liveResult]) => {
      if (cancelled) return;
      const nextAgents = agentsResult.status === "fulfilled" ? agentsResult.value : [];
      const nextLive = liveResult.status === "fulfilled" ? liveResult.value : null;
      setFallback({
        agentCockpit: agentCockpit ?? null,
        agentLive: nextLive,
        agents: nextAgents,
        status: nextAgents.length || nextLive ? "active" : "failed",
      });
      if (agentCockpit) return;
      cockpitTimer = window.setTimeout(() => {
        void getAgentCockpit(projectId).then((nextCockpit) => {
          if (cancelled) return;
          setFallback((current) => ({
            ...current,
            agentCockpit: nextCockpit,
            status: current.status === "failed" ? "active" : current.status,
          }));
        }).catch(() => undefined);
      }, nextAgents.length || nextLive ? 800 : 0);
    }).catch(() => {
      if (!cancelled) setFallback((current) => ({ ...current, status: "failed" }));
    });
    return () => {
      cancelled = true;
      if (cockpitTimer !== undefined) window.clearTimeout(cockpitTimer);
    };
  }, [agentCockpit, agentLive, agents.length, projectId]);

  const effectiveAgents = agents.length ? agents : fallback.agents;
  const effectiveAgentCockpit = agentCockpit ?? fallback.agentCockpit;
  const effectiveAgentLive = agentLive ?? fallback.agentLive;
  const projectionSource = agents.length ? "snapshot" : fallback.status === "active" ? "fallback active" : fallback.status === "loading" ? "fallback loading" : "snapshot pending";
  const workers = useMemo(
    () => effectiveAgents.filter(isBackendWorker),
    [effectiveAgents],
  );
  const attentionWorkers = useMemo(
    () => workers.filter((agent) => needsAttention(agent.attention_state)),
    [workers],
  );
  const [selectedId, setSelectedId] = useState("");
  const fleetMetrics = buildFleetMetrics(effectiveAgents, effectiveAgentCockpit, cost);
  const agentAttentionRows = buildAgentAttentionRows(effectiveAgents, effectiveAgentCockpit, recovery);
  const roleFleetRows = buildRoleFleetRows(effectiveAgents, cost);

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

  const selected = workers.find((worker) => worker.instance_id === selectedId) ?? null;
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
  const ledgerEntries = Object.entries(cost?.per_role ?? {});
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
      ? (cost?.per_role[role] ?? workerCostByRole.get(role) ?? zeroCost)
      : (workerCostByRole.get(role) ?? cost?.per_role[role] ?? zeroCost);
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
          <span className="muted">{workers.length} backend workers · {projectionSource}</span>
        </div>
        <span className="metric-chip">Autopilot cockpit</span>
      </div>

      <div className="agent-fleet-summary-grid">
        <RuntimeSummaryCard
          label="Workers"
          value={fleetMetrics.backendWorkers}
          meta={`${fleetMetrics.controlAgents} control / ${fleetMetrics.operatorAgents} operator`}
          tone={fleetMetrics.backendWorkers ? "info" : "muted"}
        />
        <RuntimeSummaryCard
          label="Health"
          value={`${fleetMetrics.stuck} stuck`}
          meta={typeof metricsSnapshot?.mtts === "number" && metricsSnapshot.mtts > 0
            ? `MTTS ${metricsSnapshot.mtts.toFixed(1)}h · recovery ${Math.round((metricsSnapshot.stuck_recovery_rate ?? 0) * 100)}% · ${fleetMetrics.silent} silent`
            : `${fleetMetrics.silent} silent / ${fleetMetrics.drift} drift`}
          tone={fleetMetrics.stuck || fleetMetrics.silent ? "err" : fleetMetrics.drift ? "warn" : "ok"}
        />
        <RuntimeSummaryCard
          label="Context"
          value={contextPercent(fleetMetrics.maxContext)}
          meta={`${fleetMetrics.contextWarn} warning`}
          tone={fleetMetrics.contextWarn ? "warn" : "ok"}
        />
        <RuntimeSummaryCard
          label="Tokens"
          value={formatTokens(fleetMetrics.totalInputTokens + fleetMetrics.totalOutputTokens)}
          meta={typeof metricsSnapshot?.cost_per_task === "number" && metricsSnapshot.cost_per_task > 0
            ? `${formatUsd(fleetMetrics.totalCostUsd)} · $${metricsSnapshot.cost_per_task.toFixed(2)}/task`
            : formatUsd(fleetMetrics.totalCostUsd)}
          tone={fleetMetrics.totalInputTokens || fleetMetrics.totalOutputTokens ? "info" : "muted"}
        />
        <RuntimeSummaryCard
          label="Providers"
          value={fleetMetrics.providerSummary}
          meta={projectionSource}
          tone={fleetMetrics.providerSummary === "-" ? "muted" : "info"}
        />
      </div>

      <section className="subsection agent-attention-cockpit">
        <div className="inline-heading">
          <h3 className="section-title">Attention Queue</h3>
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
                <span className={`badge badge-${row.severity}`}>{row.severity}</span>
                <span className="mono">{row.target}</span>
                <span>{row.reason}</span>
                <span className="muted">{row.recommended_action}</span>
                <span className="muted mono">{row.evidence}</span>
              </button>
            ))}
          </div>
        ) : (
          <p className="empty-text">
            {fleetMetrics.stale
              ? "Historical attention hidden — project runtime is archived/stopped."
              : "No agent attention required."}
          </p>
        )}
      </section>

      {workers.length ? (
        <TablePage
          title="Fleet"
          onOpen={(row) => setSelectedId(String(row.instance || ""))}
          rows={workers.map((worker) => {
            const role = worker.parent_role || worker.role_type || worker.instance_id;
            const eff = (fleetStats?.role_efficiency ?? []).find((entry) => entry.role === role);
            return {
              instance: worker.instance_id,
              backend: worker.backend || "-",
              task: worker.task_id || worker.active_task || "-",
              state: worker.stale
                ? `last known: ${worker.lifecycle_state || worker.state || "-"}`
                : (worker.lifecycle_state || worker.state || "-"),
              context: worker.context_usage_ratio == null ? "-" : `${Math.round(worker.context_usage_ratio * 100)}%`,
              tokens: formatTokens((worker.cost?.input_tokens ?? 0) + (worker.cost?.output_tokens ?? 0)),
              usd: formatUsd(worker.cost?.usd),
              done_7d: eff?.done ?? 0,
              rework: eff?.rework ?? 0,
              respawn: eff?.respawn ?? 0,
              attention: worker.attention_state || "idle",
            };
          })}
          embedded
        />
      ) : (
        <section className="subsection">
          <div className="inline-heading">
            <h3 className="section-title">Role Fleet</h3>
            <span className="muted">0 workers</span>
          </div>
          <p className="empty-text">No backend worker projection data.</p>
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
            <span className="muted">{attentionWorkers.length} workers</span>
          </div>
          {attentionWorkers.length === 0 ? (
            <p className="empty-text">No worker attention required.</p>
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
      <div className="agent-view-layout">
        <section className="subsection agent-cockpit-panel">
          <div className="inline-heading">
            <h3 className="section-title">Selected Worker</h3>
            <span className="muted">{selected.instance_id}</span>
            <button className="icon-button" type="button" onClick={() => setSelectedId("")}>
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
            <p className="empty-text">No backend worker projection data.</p>
          )}
        </section>
      </div>
      )}
    </>
  );
}
