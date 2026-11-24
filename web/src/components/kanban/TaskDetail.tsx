import type { EventRecord, ExecutionRouteProjection, RecentEvent, SkillsSummary, Task, TaskDetail as TaskDetailModel, TaskDiff } from "../../api/types";
import { contextBadgeTone, contextLabel } from "../../lib/format";
import { routeStatusTone } from "../../lib/task-display";
import { Hash, Wrench } from "lucide-react";
import { Fragment } from "react";
import type { ProjectionKind } from "../../app/sharedTypes";
import { EventTable, ExecutionRoutePanel, KeyValuePanel, PreBlock, TablePage, asRecord, asRecordArray, asStringArray, formatAge, numberValue, recordValue, stringify, textValue } from "../../app/shared";
import { latestInvalidContract } from "./taskExecution";
import { TaskInputPanel } from "./TaskInputPanel";
import { previewProfileForRef } from "../agent-session/previewRegistry";

export function ContractInvalidBanner({
  task,
  detail,
  events,
}: {
  task: Task;
  detail: TaskDetailModel | null;
  events: RecentEvent[];
}) {
  // Prefer the detail.events list (server-truncated to last 80 for this task);
  // fall back to global events filtered by task id.
  const detailEvents = (detail?.events ?? []) as EventRecord[];
  const taskEvents: EventRecord[] = detailEvents.length
    ? detailEvents
    : events.filter((e) => (e as EventRecord).task_id === task.id);

  const latestInvalid = latestInvalidContract(taskEvents);
  if (!latestInvalid) return null;

  const payload = (latestInvalid.payload ?? {}) as Record<string, unknown>;
  const errors = Array.isArray(payload.errors) ? (payload.errors as unknown[]) : [];
  const source = typeof payload.source === "string" ? payload.source : "";
  const role = typeof payload.role === "string" ? payload.role : "";

  // Detect the P2/K4 backlog-refs flavor (errors mention "required_backlog_refs").
  const isBacklogFlavor = errors.some(
    (msg) => typeof msg === "string" && msg.includes("required_backlog_refs"),
  );
  const title = isBacklogFlavor
    ? "task.contract.invalid — missing required_backlog_refs"
    : "task.contract.invalid";

  return (
    <div
      className="contract-invalid-banner"
      role="alert"
      style={{
        background: "rgba(220, 38, 38, 0.12)",
        border: "1px solid rgba(220, 38, 38, 0.4)",
        borderRadius: 8,
        padding: "10px 14px",
        margin: "8px 0",
        color: "#7f1d1d",
        fontSize: 13,
        lineHeight: 1.5,
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 4 }}>
        ⚠ {title}
        {role ? ` (role=${role})` : ""}
        {source ? ` [${source}]` : ""}
      </div>
      {errors.length > 0 ? (
        <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
          {errors.slice(0, 8).map((msg, idx) => (
            <li key={idx} style={{ marginBottom: 2 }}>
              {typeof msg === "string" ? msg : JSON.stringify(msg)}
            </li>
          ))}
          {errors.length > 8 ? (
            <li style={{ opacity: 0.7 }}>… +{errors.length - 8} more</li>
          ) : null}
        </ul>
      ) : (
        <div style={{ opacity: 0.8 }}>
          Orchestrator must re-emit task.contract.update with the missing
          fields before dev can be dispatched.
        </div>
      )}
    </div>
  );
}


// P5/W1 (docs/impl/22-zaofu-canonical-dag.md §4.3): inline badge showing
// how many of the 6 required_backlog_refs are populated. Surfaces P0-P4
// backlog synthesis state to the operator without opening task detail.
//
// Display modes:
//   - all 6 present:   "📋 6/6"  (green)
//   - partial:         "📋 N/6"  (amber)  + tooltip lists the missing refs
//   - all empty AND task is in design/design_critique stage: "📋 pre-backlog"
//     (gray, intentional — orchestrator hasn't synthesized yet)
//   - all empty AND task targets dev: "📋 0/6"  (red, broken)


function operationStateTone(status: string | undefined): "ok" | "warn" | "err" | "muted" | "info" {
  if (!status) return "muted";
  if (["done", "completed", "passed"].includes(status)) return "ok";
  if (["blocked", "failed", "cancelled"].includes(status)) return "err";
  if (["needs_recovery", "rework"].includes(status)) return "warn";
  if (["in_progress", "progressed", "running"].includes(status)) return "info";
  return "muted";
}


function providerTone(status: string | undefined): "ok" | "warn" | "err" | "muted" | "info" {
  if (!status || status === "unknown") return "muted";
  if (["ok", "healthy", "ready"].includes(status)) return "ok";
  if (["blocked", "exhausted", "failed"].includes(status)) return "err";
  if (["degraded", "cooldown", "limited"].includes(status)) return "warn";
  return "info";
}


function handoffTone(summary: Record<string, unknown>): "ok" | "warn" | "err" | "muted" | "info" {
  if (asStringArray(summary.blockers).length) return "err";
  if (asRecordArray(summary.missing_evidence).length) return "warn";
  if (textValue(summary.current_state) === "done") return "ok";
  if (textValue(summary.next_required_action)) return "info";
  return "muted";
}


export function TaskActivityPanel({
  detail,
  events,
  onOpenProjection,
  route,
  timelineError,
  timelineLoading,
  traceId,
}: {
  detail: TaskDetailModel | null;
  events: EventRecord[];
  onOpenProjection: (kind: ProjectionKind, id: string) => void;
  route: ExecutionRouteProjection | null;
  timelineError: string | null;
  timelineLoading: boolean;
  traceId: string;
}) {
  return (
    <div className="task-activity-view" data-testid="task-activity-view">
      <ExecutionRoutePanel
        error={timelineError}
        loading={timelineLoading}
        onOpenProjection={onOpenProjection}
        route={route}
        traceId={traceId}
      />
      <section className="subsection">
        <div className="inline-heading"><h3>Recent Task Events</h3><span className="muted">{events.length} events</span></div>
        <EventTable events={events} compact />
      </section>
      <TaskExecutionPanel detail={detail} onOpenProjection={onOpenProjection} />
    </div>
  );
}


export function TaskEvidencePanel({ detail, diff, projectId }: { detail: TaskDetailModel | null; diff: TaskDiff | null; projectId?: string }) {
  const operation = asRecord(detail?.task_run_panel?.active_operation);
  const pipelineAttempt = textValue(asRecord(detail?.handoff_summary?.pipeline).attempt_id);
  const attemptId = detail?.handoff_summary?.pipeline ? pipelineAttempt
    : textValue(detail?.task?.active_dispatch_id) || textValue(operation.attempt_id);
  return (
    <div className="task-evidence-view" data-testid="task-evidence-view">
      <TaskInputPanel key={`${projectId}:${detail?.task?.id}:${attemptId}`} attemptId={attemptId} projectId={projectId} />
      <ArtifactLedgerPanel detail={detail} />
      <section className="subsection">
        <h3>Git</h3>
        <PreBlock value={detail?.git ?? {}} />
        {diff?.files.length ? (
          <div className="compact-list">{diff.files.map((file) => <span className="mono" key={file}>{file}</span>)}</div>
        ) : null}
        {diff?.error ? <p className="empty-text">{diff.error}</p> : null}
        {diff?.diff ? <pre className="text-block diff-block">{diff.diff}</pre> : null}
      </section>
      <section className="subsection"><h3>Verify</h3><PreBlock value={detail?.verify ?? {}} /></section>
      <section className="subsection"><h3>Review</h3><PreBlock value={detail?.review ?? {}} /></section>
    </div>
  );
}


export function TaskAdvancedPanel({
  detail,
  skillsSummary,
  task,
}: {
  detail: TaskDetailModel | null;
  skillsSummary: SkillsSummary | null;
  task: Task;
}) {
  const workdir = asRecord(detail?.workdir);
  const statusModel = asRecord(detail?.status_model);
  const evidenceModel = asRecord(detail?.evidence_model);
  const interactionEvidence = asRecord(evidenceModel.interaction);
  const currentSession = asRecord(interactionEvidence.current_session);
  const sessionRows = [
    { key: "role_session", value: detail?.role_instance || "-" },
    { key: "agent_scope", value: `project + context:${task.id}` },
    { key: "workdir", value: textValue(workdir.project_path || workdir.workdir) || "-" },
    { key: "branch", value: textValue(workdir.branch || workdir.branch_or_ref || task.git?.branch) || "-" },
    { key: "session", value: textValue(currentSession.status) || "-" },
    { key: "transcripts", value: numberValue(interactionEvidence.transcript_count) ?? 0 },
  ];
  const statusRows = [
    { key: "canonical", value: statusModel.canonical_task_status || "task.status" },
    { key: "source", value: statusModel.task_status_source || "TaskStore/EventWriter" },
    { key: "task_status", value: statusModel.task_status || task.status },
    { key: "run_done", value: statusModel.run_completed_implies_task_done === false ? "evidence only" : stringify(statusModel.run_completed_implies_task_done) },
    { key: "done_requires", value: statusModel.done_requires || "-" },
  ];
  return (
    <div className="task-advanced-view task-workbench" data-testid="task-advanced-view">
      <div className="workbench-grid evidence-grid">
        <KeyValuePanel title="Session" rows={sessionRows} />
        <KeyValuePanel title="Status Model" rows={statusRows} />
      </div>
      <TaskEffectiveSkillsPanel skillsSummary={skillsSummary} task={task} />
      <section className="subsection"><h3>Contract</h3><PreBlock value={detail?.contract ?? task} /></section>
      <section className="subsection">
        <h3>Briefing</h3>
        {detail?.briefing?.text ? <pre className="text-block">{detail.briefing.text}</pre> : <p className="empty-text">No briefing projection.</p>}
      </section>
      <section className="subsection"><h3>Diagnostics</h3><PreBlock value={detail?.diagnostics ?? {}} /></section>
    </div>
  );
}


export function TaskRunSummaryPanel({
  detail,
  onOpenProjection,
  task,
}: {
  detail: TaskDetailModel | null;
  onOpenProjection: (kind: ProjectionKind, id: string) => void;
  task: Task;
}) {
  const panel = asRecord(detail?.task_run_panel);
  const active = asRecord(panel.active_operation);
  const lastEvent = asRecord(active.last_event);
  const latest = asRecord(panel.latest_progress);
  const health = asRecord(panel.health);
  const counts = asRecord(panel.counts);
  const route = asRecord(panel.route_summary);
  const handoff = asRecord(detail?.handoff_summary);
  const contextRatio = numberValue(health.context_usage_ratio);
  const state = textValue(active.state) || textValue(panel.status) || task.status;
  const traceId = detail?.links?.trace || detail?.trace_id || task.links?.trace || "";
  const routeStatus = textValue(route.status);
  const stageLabel = textValue(route.current_stage_label) || textValue(route.current_stage) || textValue(panel.current_stage) || "stage unknown";
  const pipelineOwner = detail?.handoff_summary?.pipeline ? asRecord(handoff.owner) : null;
  const actorLabel = pipelineOwner ? textValue(pipelineOwner.instance_id) || "等待接手" : [
    textValue(active.role) || textValue(panel.role_instance) || task.assigned_to || "unassigned",
    textValue(active.instance_id),
  ].filter(Boolean).join(" / ");
  const latestMessage = textValue(latest.message || latest.current_subtask);
  const nextEvent = textValue(handoff.next_required_event);
  const nextAction = textValue(handoff.next_required_action);
  const missingCount = asRecordArray(handoff.missing_evidence).length;
  const blockerCount = asStringArray(handoff.blockers).length;
  const changedCount = asStringArray(handoff.changed_files).length;
  const completedCount = asRecordArray(handoff.completed).length;
  const eventCount = numberValue(counts.events) ?? 0;
  const operationCount = numberValue(counts.operations) ?? 0;
  const runCount = numberValue(counts.runs) ?? 0;

  return (
    <section className="task-run-summary-panel task-run-header">
      <div className="task-run-header-main">
        <div className="task-run-title-row">
          <span className={`badge badge-${routeStatusTone(routeStatus)}`}>{stageLabel}</span>
          <strong>Current run</strong>
          <span className="muted truncate-value">{actorLabel}</span>
        </div>
        <div className="button-row task-run-actions">
          <span className={`badge badge-${operationStateTone(state)}`}>run {state || "unknown"}</span>
          {traceId ? (
            <button className="icon-button" type="button" onClick={() => onOpenProjection("trace", traceId)}>
              Trace
            </button>
          ) : null}
        </div>
      </div>
      <div className="task-run-status-row">
        <span className={`badge badge-${contextBadgeTone(contextRatio)}`}>{contextLabel(contextRatio)}</span>
        <span className={`badge badge-${providerTone(textValue(health.provider_status))} task-run-provider-badge`}>
          provider {textValue(health.provider_status) || "unknown"}
        </span>
        {health.heartbeat_age_seconds != null ? (
          <span className="badge badge-muted">hb {formatAge(numberValue(health.heartbeat_age_seconds))}</span>
        ) : null}
        <span className="badge badge-muted">{eventCount} events</span>
        {operationCount || runCount ? (
          <span className="badge badge-muted task-run-ops-badge">{operationCount} ops · {runCount} runs</span>
        ) : null}
        {textValue(lastEvent.type) ? <span className="badge badge-muted task-run-last-badge">last {textValue(lastEvent.type)}</span> : null}
      </div>
      <div className="task-run-handoff-strip">
        <span className={`badge badge-${handoffTone(handoff)}`}>
          handoff {textValue(handoff.current_state) || "unknown"}
        </span>
        <span className="task-run-next">
          <span className="muted">next</span>
          <span className="mono truncate-value">{nextEvent || "-"}</span>
          {nextAction ? <span className="truncate-value">{nextAction}</span> : null}
        </span>
        {missingCount ? <span className="badge badge-warn">{missingCount} missing</span> : null}
        {blockerCount ? <span className="badge badge-err">{blockerCount} blockers</span> : null}
        {completedCount ? <span className={`badge badge-${detail?.handoff_summary?.pipeline ? "info" : "ok"}`}>{completedCount} {detail?.handoff_summary?.pipeline ? "admitted" : "done"}</span> : null}
        {changedCount ? <span className="badge badge-muted task-run-files-badge">{changedCount} files</span> : null}
      </div>
      {latestMessage ? <p className="task-run-latest">{latestMessage}</p> : null}
    </section>
  );
}


export function HandoffSummaryPanel({ detail }: { detail: TaskDetailModel | null }) {
  const summary = asRecord(detail?.handoff_summary);
  const completed = asRecordArray(summary.completed).slice(0, 4);
  const missing = asRecordArray(summary.missing_evidence).slice(0, 4);
  const receipt = asRecord(summary.receipt);
  const receiptStatus = textValue(receipt.status);
  const receiptLabel: Record<string, string> = {
    unavailable: "尚无交接材料",
    awaiting_receipt: "材料已保存，等待接收确认",
    accepted: "接手者已确认接收（不代表业务完成）",
    incomplete: "交接证据不完整，需要修复",
  };

  const hasReceipt = Boolean(receiptStatus && receiptStatus !== "unavailable");
  const hasDelivery = Boolean(receipt.delivery_status);
  if (!completed.length && !missing.length && !hasReceipt && !hasDelivery) return null;

  return (
    <section className="subsection handoff-summary-panel">
      <div className="inline-heading">
        <h3>交接</h3>
        <span className={`badge badge-${handoffTone(summary)}`}>
          {textValue(summary.current_state) || "unknown"}
        </span>
      </div>
      {(hasReceipt || hasDelivery) && <dl className="detail-grid compact-detail-grid task-run-grid">
        {hasReceipt && <>
          <dt>接收状态</dt>
          <dd data-testid="task-handoff-receipt">{receiptLabel[receiptStatus] || receiptStatus}</dd>
        </>}
        {hasDelivery ? <>
          <dt>交接派发</dt>
          <dd>{receipt.delivery_status === "dispatched" ? "已派发到目标执行" : "尚未派发到目标执行"}</dd>
        </> : null}
      </dl>}
      {completed.length ? (
        <div className="compact-list">
          {completed.map((item, index) => (
            <span className="handoff-line" key={`done-${index}`}>
              <span className={`badge badge-${item.kind === "admitted_result" ? "info" : "ok"}`}>{item.kind === "admitted_result" ? "admitted" : "done"}</span>
              <span>{textValue(item.summary || item.kind || item.type)}</span>
            </span>
          ))}
        </div>
      ) : null}
      {missing.length > 0 && <ul>{missing.map((item, index) => <li key={index}>
        缺少依据 · {textValue(item.message || item.kind || item.expected)}
      </li>)}</ul>}
    </section>
  );
}


const CONTRACT_REF_LABELS: Record<string, string> = {
  spec_ref: "Spec",
  plan_ref: "Plan",
  tdd_ref: "TDD",
  task_map_ref: "Task Map",
  backlog_plan_ref: "Backlog",
  process_plan_ref: "Process",
  critic_event_id: "Critic Event",
  critic_gate_ref: "Critic Gate",
};


function TaskEffectiveSkillsPanel({
  skillsSummary,
  task,
}: {
  skillsSummary: SkillsSummary | null;
  task: Task;
}) {
  const rows = buildTaskEffectiveSkillRows(task, skillsSummary);
  return (
    <section className="subsection task-effective-skills-panel">
      <div className="inline-heading">
        <h3>Effective Skills</h3>
        <span className="muted">{rows.length} projected</span>
      </div>
      <TablePage
        title="Effective Skills"
        rows={rows}
        embedded
        emptyState={{
          title: "No effective skills",
          description: "This task has no required skills and no role skill projection for the current assignee.",
          icon: Wrench,
          compact: true,
        }}
      />
    </section>
  );
}


function buildTaskEffectiveSkillRows(
  task: Task,
  skillsSummary: SkillsSummary | null,
): Record<string, unknown>[] {
  const required = new Set((task.skills_required ?? []).filter(Boolean));
  const assignee = task.assigned_to || "";
  const enabledRows = (skillsSummary?.enabled ?? []).filter((row) => (
    !assignee || row.role === assignee || row.role_name === assignee
  ));
  for (const row of enabledRows) {
    for (const skill of row.skills ?? []) {
      if (skill) required.add(skill);
    }
  }

  const loadedRows = (skillsSummary?.loaded ?? []).map(asRecord);
  const lockRows = (skillsSummary?.lock ?? []).map(asRecord);
  const invocationRows = (skillsSummary?.invocation?.skills ?? []).map(asRecord);
  const poolRows = skillsSummary?.pool ?? [];
  return [...required].sort((left, right) => left.localeCompare(right)).map((skill) => {
    const loaded = findTaskSkillRecord(loadedRows, task, skill);
    const lock = findTaskSkillRecord(lockRows, task, skill);
    const invocation = findTaskSkillRecord(invocationRows, task, skill);
    const pool = poolRows.find((row) => row.name === skill) ?? null;
    const source = textValue(loaded?.source)
      || textValue(lock?.source)
      || pool?.path
      || "-";
    const materializedTo = textValue(loaded?.materialized_to)
      || textValue(lock?.materialized_to);
    const autoInject = boolish(loaded?.auto_inject ?? lock?.auto_inject);
    const loadOnDemand = loaded?.load_on_demand == null && lock?.load_on_demand == null
      ? true
      : boolish(loaded?.load_on_demand ?? lock?.load_on_demand);
    const injected = autoInject
      ? "auto"
      : materializedTo
        ? "materialized"
        : loadOnDemand
          ? "load-on-demand"
          : "index";
    return {
      skill,
      role: textValue(loaded?.role) || textValue(lock?.role) || enabledRows[0]?.role || "-",
      backend: textValue(loaded?.backend) || textValue(lock?.backend) || enabledRows[0]?.backend || "-",
      status: textValue(loaded?.status) || textValue(lock?.status) || (source !== "-" ? "enabled" : "requested"),
      observed: textValue(invocation?.observation) || "unobserved",
      invoked: boolish(invocation?.invoked),
      evidence_events: Array.isArray(invocation?.evidence) ? invocation.evidence.length : 0,
      dispatch_id: textValue(invocation?.dispatch_id) || "-",
      injected,
      load_on_demand: loadOnDemand,
      source,
      sha256: textValue(loaded?.sha256) || textValue(lock?.sha256) || pool?.sha256 || "-",
      materialized_to: materializedTo || "-",
    };
  });
}


function findTaskSkillRecord(
  rows: Record<string, unknown>[],
  task: Task,
  skill: string,
): Record<string, unknown> | null {
  const assignee = task.assigned_to || "";
  for (const row of rows.slice().reverse()) {
    const rowName = textValue(row.name) || textValue(row.skill) || textValue(row.skill_name);
    if (rowName !== skill) continue;
    const rowTaskId = textValue(row.task_id);
    if (rowTaskId && rowTaskId !== task.id) continue;
    const rowRole = textValue(row.role) || textValue(row.role_name);
    const rowInstance = textValue(row.instance_id);
    if (!assignee || rowRole === assignee || rowInstance === assignee) {
      return row;
    }
  }
  return null;
}


function boolish(value: unknown): boolean {
  if (value === true) return true;
  if (typeof value === "string") return value.toLowerCase() === "true";
  return false;
}


function ArtifactLedgerPanel({ detail }: { detail: TaskDetailModel | null }) {
  const ledger = asRecord(detail?.artifact_refs);
  const artifacts = Array.isArray(ledger.artifact_refs)
    ? ledger.artifact_refs.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
  const accepted = Array.isArray(ledger.accepted_artifact_refs)
    ? ledger.accepted_artifact_refs.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
  const stale = Array.isArray(ledger.stale_artifact_refs)
    ? ledger.stale_artifact_refs.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
  const hashRows = Array.isArray(ledger.hash_status)
    ? ledger.hash_status.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
  const contractRefs = asRecord(ledger.contract_refs);
  const hasLedger = artifacts.length > 0 || Object.keys(contractRefs).length > 0;
  if (!hasLedger) return null;
  const hashByArtifact = new Map<string, Record<string, unknown>>();
  for (const row of hashRows) {
    const key = textValue(row.artifact_id) || textValue(row.path);
    if (key) hashByArtifact.set(key, row);
  }
  const taskMapSummary = asRecord(ledger.task_map_summary);
  const tasksByWave = asRecord(taskMapSummary.tasks_by_wave);
  const taskMapErrors = asStringArray(taskMapSummary.errors);
  const diagnostics = asRecordArray(ledger.diagnostics);
  const contractRefEntries = Object.entries(contractRefs).filter(
    ([, value]) => textValue(value),
  );
  const hasTaskMapSummary = Object.keys(taskMapSummary).length > 0;
  return (
    <div className="subsection">
      <div className="inline-heading">
        <h3>Artifact Ledger</h3>
        <span className="muted">{accepted.length} accepted · {stale.length} stale</span>
      </div>
      {diagnostics.length ? (
        <div className="badge-row">
          {diagnostics.map((diag, index) => {
            const severity = textValue(diag.severity) || "warning";
            const diagType = textValue(diag.type);
            const label = textValue(diag.message)
              || (diagType === "artifact_hash_failure"
                ? `${numberValue(diag.count) ?? 0} artifact hash failure(s)`
                : diagType === "artifact_manifest_missing"
                  ? "Artifact manifest missing — fallback heuristics in use"
                  : diagType);
            return (
              <span
                key={`${diagType}-${index}`}
                className={`badge ${severity === "error" ? "badge-err" : "badge-warn"}`}
              >
                {label}
              </span>
            );
          })}
        </div>
      ) : null}
      <div className="detail-grid compact-detail-grid">
        <dt>Manifest</dt>
        <dd className="mono">{stringify(ledger.manifest_event_id)}</dd>
        <dt>Role</dt>
        <dd>{stringify(ledger.manifest_role)}</dd>
      </div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Kind</th>
              <th>Path</th>
              <th>Version</th>
              <th>Status</th>
              <th>Hash</th>
            </tr>
          </thead>
          <tbody>
            {artifacts.slice(0, 40).map((artifact, index) => {
              const key = textValue(artifact.artifact_id) || textValue(artifact.path);
              const hash = hashByArtifact.get(key) || {};
              const hashStatus = textValue(hash.status || "unknown");
              const ledgerStatus = textValue(artifact.status || "accepted");
              const profile = previewProfileForRef(artifact);
              const badgeClass = hashStatus === "mismatch" || hashStatus === "missing"
                ? "badge badge-err"
                : ledgerStatus === "superseded" || ledgerStatus === "rejected"
                  ? "badge badge-warn"
                  : "badge";
              return (
                <tr key={`${key}-${index}`}>
                  <td>{stringify(artifact.kind)}</td>
                  <td className="mono">
                    <details className="artifact-preview-inline">
                      <summary>{stringify(artifact.path) || stringify(key)}</summary>
                      <dl>
                        <dt>profile</dt><dd>{profile}</dd>
                        <dt>artifact</dt><dd>{stringify(key)}</dd>
                        <dt>event</dt><dd>{stringify(artifact.event_id)}</dd>
                        <dt>ref</dt><dd>{stringify(artifact.uri || artifact.path)}</dd>
                      </dl>
                    </details>
                  </td>
                  <td>{stringify(artifact.version)}</td>
                  <td><span className={badgeClass}>{ledgerStatus}</span></td>
                  <td><span className={badgeClass}>{hashStatus}</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {contractRefEntries.length ? (
        <div className="detail-grid compact-detail-grid">
          {contractRefEntries.map(([key, value]) => (
            <Fragment key={key}>
              <dt>{CONTRACT_REF_LABELS[key] || key}</dt>
              <dd className="mono">{textValue(value)}</dd>
            </Fragment>
          ))}
        </div>
      ) : null}
      {hasTaskMapSummary ? (
        <div className="subsection">
          <div className="inline-heading">
            <h4>Final Task Map</h4>
            <span className={taskMapSummary.passed === false ? "badge badge-err" : "muted"}>
              {taskMapSummary.passed === false ? "validation failed" : "validated"}
            </span>
          </div>
          <div className="detail-grid compact-detail-grid">
            <dt>Tasks</dt>
            <dd>{numberValue(taskMapSummary.task_count) ?? 0}</dd>
            <dt>Waves</dt>
            <dd>{numberValue(taskMapSummary.wave_count) ?? 0}</dd>
            <dt>Exclusive files</dt>
            <dd>{numberValue(taskMapSummary.exclusive_file_count) ?? 0}</dd>
          </div>
          {Object.keys(tasksByWave).length ? (
            <p className="muted">
              {Object.entries(tasksByWave)
                .map(([wave, count]) => `${wave}: ${numberValue(count) ?? 0}`)
                .join(" · ")}
            </p>
          ) : null}
          {taskMapErrors.length ? (
            <div className="task-map-errors">
              {taskMapErrors.slice(0, 8).map((err, index) => (
                <p key={index} className="empty-text compact-error">{err}</p>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}


function TaskExecutionPanel({
  detail,
  onOpenProjection,
}: {
  detail: TaskDetailModel | null;
  onOpenProjection: (kind: ProjectionKind, id: string) => void;
}) {
  const runs = detail?.runs ?? [];
  const activeRuns = runs.filter((row) => !textValue(row.ended_at) && textValue(row.status) !== "completed");
  const historyRuns = runs.slice(0, 12);

  return (
    <div className="subsection">
      <div className="inline-heading">
        <h3>Execution</h3>
        <span className="muted">{activeRuns.length} active · {historyRuns.length} history</span>
      </div>
      <div className="project-grid two compact-execution-grid">
        <TablePage
          embedded
          title="Active Execution"
          rows={activeRuns}
          onOpen={(row) => {
            const runId = textValue(row.run_id);
            if (runId) onOpenProjection("run", runId);
          }}
        />
        <TablePage
          embedded
          title="Execution History"
          rows={historyRuns}
          onOpen={(row) => {
            const runId = textValue(row.run_id);
            if (runId) onOpenProjection("run", runId);
          }}
        />
      </div>
    </div>
  );
}
