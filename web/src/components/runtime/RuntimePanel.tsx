// RuntimePanel + exclusive closure, extracted verbatim from App.tsx (P1 split).
import type { Snapshot } from "../../api/types";
import { formatTokens } from "../../lib/format";
import { KeyValuePanel, PreBlock, RuntimeDetailSection, RuntimeSummaryCard, TablePage, asRecord, compactPath, formatUsd, textValue } from "../../app/shared";

export function RuntimePanel({
  actionReady,
  actionState,
  activeProjectId,
  snapshot,
}: {
  actionReady: boolean;
  actionState: string;
  activeProjectId: string;
  snapshot: Snapshot | null;
}) {
  const agents = snapshot?.agents ?? snapshot?.roles ?? [];
  const costRoles = Object.entries(snapshot?.cost.per_role ?? {}).sort(([a], [b]) =>
    a.localeCompare(b),
  );
  const loadedSkills = snapshot?.skills.loaded ?? [];
  const skillRows = [
    { key: "loaded_skills", value: loadedSkills.length },
    { key: "enabled_roles", value: snapshot?.skills.enabled.length ?? 0 },
    { key: "skill_pool", value: snapshot?.skills.pool.length ?? 0 },
    { key: "warnings", value: snapshot?.skills.warnings.length ?? 0 },
    { key: "lock_file", value: snapshot?.skills.lock_file ?? "-" },
  ];
  const costRows = costRoles.map(([role, cost]) => ({
    role,
    usd: formatUsd(cost.usd),
    entries: cost.entries,
    input_tokens: cost.input_tokens,
    output_tokens: cost.output_tokens,
  }));
  const pauseLifecycle = asRecord(snapshot?.pause_lifecycle);
  const pauseCurrent = asRecord(pauseLifecycle.current);
  const pauseSummary = asRecord(pauseLifecycle.summary);
  const resumeSweep = asRecord(pauseLifecycle.resume_sweep);
  const pauseRows = [
    { key: "status", value: textValue(pauseLifecycle.status) || "running" },
    { key: "dispatch_allowed", value: pauseLifecycle.dispatch_allowed !== false },
    { key: "reason", value: textValue(pauseCurrent.reason) || "-" },
    { key: "pause_event", value: textValue(pauseCurrent.pause_event_id) || "-" },
    { key: "resume_event", value: textValue(pauseCurrent.resume_event_id) || "-" },
    { key: "affected_sessions", value: Number(pauseSummary.affected_sessions ?? 0) },
    { key: "checkpoints", value: Number(pauseSummary.checkpoints ?? 0) },
    { key: "resume_signals", value: Number(pauseSummary.resume_signals ?? 0) },
  ];
  const affectedSessionRows = (
    Array.isArray(pauseLifecycle.affected_sessions) ? pauseLifecycle.affected_sessions : []
  ).map((item) => {
    const row = asRecord(item);
    return {
      event: textValue(row.event_type) || "-",
      session_id: textValue(row.session_id) || "-",
      run_id: textValue(row.run_id) || "-",
      thread_id: textValue(row.thread_id) || "-",
      provider: textValue(row.provider) || "-",
      during_pause: Boolean(row.during_pause),
      reason: textValue(row.reason) || "-",
    };
  });
  const checkpointRows = (
    Array.isArray(pauseLifecycle.checkpoints) ? pauseLifecycle.checkpoints : []
  ).map((item) => {
    const row = asRecord(item);
    return {
      task_id: textValue(row.task_id) || "-",
      instance_id: textValue(row.instance_id) || "-",
      checkpoint_id: textValue(row.checkpoint_id) || "-",
      resume_packet_path: textValue(row.resume_packet_path) || "-",
      during_pause: Boolean(row.during_pause),
    };
  });
  const resumeSuggestionRows = (
    Array.isArray(resumeSweep.suggestions) ? resumeSweep.suggestions : []
  ).map((item) => {
    const row = asRecord(item);
    return {
      recovery: textValue(row.recommended_recovery) || "-",
      task_id: textValue(row.task_id) || "-",
      instance_id: textValue(row.instance_id) || "-",
      trigger_event_id: textValue(row.trigger_event_id) || "-",
      reason: textValue(row.reason) || "-",
    };
  });
  const runtimeLive = snapshot?.runtime.live === true;
  const runtimeState = !snapshot ? "pending" : runtimeLive ? "live" : "stopped";
  const runtimeTone = !snapshot ? "muted" : runtimeLive ? "ok" : "warn";
  const totalTokens = (snapshot?.cost
    ? Object.values(snapshot.cost.per_role ?? {}).reduce((total, role) => total + role.input_tokens + role.output_tokens, 0)
    : 0);
  const pauseStatus = textValue(pauseLifecycle.status) || "running";
  const pauseTone = pauseLifecycle.dispatch_allowed === false ? "warn" : "ok";
  const pauseDetailCount = affectedSessionRows.length + checkpointRows.length + resumeSuggestionRows.length;
  const stateDir = snapshot?.project.state_dir || "";

  return (
    <>
      <div className="section-heading">
        <div>
          <h2>Runtime</h2>
          <span className="muted">kernel-owned read model</span>
        </div>
      </div>
      <div className="runtime-connection-strip" aria-label="Runtime connection summary">
        <span><strong>API</strong><em className={`badge badge-${snapshot ? "ok" : "warn"}`}>{snapshot ? "connected" : "pending"}</em></span>
        <span><strong>SSE</strong><em className={`badge badge-${runtimeLive ? "ok" : "warn"}`}>{runtimeLive ? "live" : "degraded"}</em></span>
        <span><strong>Action Token</strong><em>{actionReady ? "valid" : actionState}</em></span>
        <span><strong>Runtime</strong><em>{runtimeState}</em></span>
        <span><strong>Project</strong><em className="mono">{activeProjectId || snapshot?.project.project_id || "-"}</em></span>
        <span><strong>State Dir</strong><em className="mono" title={stateDir}>{stateDir ? compactPath(stateDir) : "-"}</em></span>
      </div>
      <div className="runtime-summary-grid">
        <RuntimeSummaryCard
          label="Runtime"
          value={runtimeState}
          meta={snapshot?.runtime.mode || "project snapshot"}
          tone={runtimeTone}
        />
        <RuntimeSummaryCard
          label="Agents"
          value={agents.length}
          meta={`${snapshot?.workers.length ?? 0} workers`}
          tone={agents.length ? "info" : "muted"}
        />
        <RuntimeSummaryCard
          label="Skills"
          value={loadedSkills.length}
          meta={`${snapshot?.skills.enabled.length ?? 0} enabled roles`}
          tone={loadedSkills.length ? "info" : "muted"}
        />
        <RuntimeSummaryCard
          label="Tokens"
          value={formatTokens(totalTokens)}
          meta={formatUsd(snapshot?.cost.total_usd ?? 0)}
          tone={totalTokens ? "info" : "muted"}
        />
        <RuntimeSummaryCard
          label="Pause"
          value={pauseStatus}
          meta={`${pauseDetailCount} lifecycle rows`}
          tone={pauseTone}
        />
      </div>
      <div className="runtime-health-strip" aria-label="Runtime health summary">
        <span><strong>State</strong><em className={`badge badge-${runtimeTone}`}>{runtimeState}</em></span>
        <span><strong>Dispatch</strong><em>{pauseLifecycle.dispatch_allowed === false ? "paused" : "allowed"}</em></span>
        <span><strong>Agents</strong><em>{agents.length} total / {snapshot?.workers.length ?? 0} workers</em></span>
        <span><strong>Tokens</strong><em>{formatTokens(totalTokens)}</em></span>
        <span><strong>Pause</strong><em>{pauseDetailCount} lifecycle rows</em></span>
      </div>
      <div className="runtime-primary-grid">
        <KeyValuePanel title="Runtime Skills" rows={skillRows} />
        <KeyValuePanel title="Pause Lifecycle" rows={pauseRows} />
      </div>
      <RuntimeDetailSection
        title="Agents And Cost"
        meta={`${agents.length} agents / ${costRows.length} cost rows`}
        defaultOpen={agents.length > 0 || costRows.length > 0}
      >
        <TablePage title="Runtime Agents" rows={agents} embedded />
        <TablePage title="Runtime Cost" rows={costRows} embedded />
      </RuntimeDetailSection>
      <RuntimeDetailSection
        title="Skills Detail"
        meta={`${loadedSkills.length} loaded skills`}
        defaultOpen={loadedSkills.length > 0}
      >
        <TablePage title="Loaded Runtime Skills" rows={loadedSkills} embedded />
      </RuntimeDetailSection>
      <RuntimeDetailSection
        title="Pause Recovery Detail"
        meta={`${pauseDetailCount} rows`}
        defaultOpen={pauseDetailCount > 0}
      >
        <TablePage title="Affected Headless Sessions" rows={affectedSessionRows} embedded />
        <TablePage title="Maintenance Checkpoints" rows={checkpointRows} embedded />
        <TablePage title="Resume Sweep Suggestions" rows={resumeSuggestionRows} embedded />
      </RuntimeDetailSection>
      <RuntimeDetailSection title="Advanced Projection" meta="Runtime Raw" defaultOpen={false}>
        <PreBlock value={snapshot?.runtime ?? {}} />
      </RuntimeDetailSection>
    </>
  );
}


