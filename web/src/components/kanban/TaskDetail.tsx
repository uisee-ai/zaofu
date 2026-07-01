// TaskDetail + exclusive closure, extracted verbatim from App.tsx (P1 split).
import { DETAIL_TABS, OPERATOR_BACKENDS } from "../../app/sharedTypes";
import type { ActionResponse, EventRecord, RecentEvent, SkillsSummary, Task, TaskDetail as TaskDetailModel, TaskDiff, TaskTimeline } from "../../api/types";
import { contextBadgeTone, contextLabel } from "../../lib/format";
import { routeStatusTone, taskPriority } from "../../lib/task-display";
import { Hash, Wrench } from "lucide-react";
import { Fragment, useEffect, useState } from "react";
import type { DetailTab, ProjectionKind } from "../../app/sharedTypes";
import { EventTable, ExecutionRoutePanel, KeyValuePanel, PreBlock, TablePage, asRecord, asRecordArray, asStringArray, csvList, formatAge, numberValue, recordValue, stringify, textValue } from "../../app/shared";
import { previewProfileForRef } from "../agent-session/previewRegistry";

type TaskActionHandler = (action: string, payload?: Record<string, unknown>) => void;


interface TaskEditDraft {
  title: string;
  status: string;
  priority: string;
  assignedTo: string;
  blockedReason: string;
  blockedBy: string;
  skills: string;
  behavior: string;
  verification: string;
  scope: string;
  exclusions: string;
  acceptance: string;
  reworkTo: string;
}


function joinCsv(value: unknown): string {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean).join(", ") : "";
}


function taskEditDraft(task: Task, detail: TaskDetailModel | null): TaskEditDraft {
  const taskRecord = task as Task & Record<string, unknown>;
  const contract = recordValue(detail?.contract) ?? recordValue(taskRecord.contract) ?? {};
  return {
    title: task.title || "",
    status: task.status || "backlog",
    priority: String(taskPriority(task)),
    assignedTo: task.assigned_to || "",
    blockedReason: task.blocked_reason || "",
    blockedBy: joinCsv(task.blocked_by),
    skills: joinCsv(task.skills_required),
    behavior: textValue(contract.behavior),
    verification: textValue(contract.verification),
    scope: joinCsv(contract.scope),
    exclusions: joinCsv(contract.exclusions),
    acceptance: textValue(contract.acceptance || "exit_code=0"),
    reworkTo: textValue(contract.rework_to),
  };
}


function ContractInvalidBanner({
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

  let latestInvalid: EventRecord | null = null;
  for (let i = taskEvents.length - 1; i >= 0; i--) {
    const e = taskEvents[i];
    if (!e || !e.type) continue;
    if (e.type === "task.contract.invalid") {
      latestInvalid = e;
      break;
    }
    // If a later update/dispatch supersedes the invalid, no banner.
    if (e.type === "task.contract.update" || e.type === "task.dispatched") {
      return null;
    }
  }
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


export function TaskDetail({
  actionReady,
  actionResult,
  actionState,
  detail,
  diff,
  events,
  loadError,
  onAction,
  onBackToBoard,
  onOpenOrchestrator,
  onOpenProjection,
  selectedTaskId,
  setTab,
  skillsSummary,
  tab,
  task,
  timeline,
  timelineError,
  timelineLoading,
}: {
  actionReady: boolean;
  actionResult: ActionResponse | null;
  actionState: string;
  detail: TaskDetailModel | null;
  diff: TaskDiff | null;
  events: RecentEvent[];
  loadError: string | null;
  onAction: TaskActionHandler;
  onBackToBoard: () => void;
  onOpenOrchestrator: () => void;
  onOpenProjection: (kind: ProjectionKind, id: string) => void;
  selectedTaskId: string | null;
  setTab: (tab: DetailTab) => void;
  skillsSummary: SkillsSummary | null;
  tab: DetailTab;
  task: Task | null;
  timeline: TaskTimeline | null;
  timelineError: string | null;
  timelineLoading: boolean;
}) {
  const taskEvents = detail?.events?.length ? detail.events : events.slice(0, 40);
  const timelineRoute = timeline?.execution_route ?? detail?.execution_route ?? null;
  const timelineTraceId = timeline?.links?.trace || timeline?.trace_id || detail?.links?.trace || detail?.trace_id || task?.links?.trace || "";

  if (!task) {
    return (
      <section className="task-detail">
        <div className="section-heading">
          <h2>Task</h2>
          <button className="icon-button" type="button" onClick={onBackToBoard}>
            Board
          </button>
        </div>
        <p className="empty-text">
          {selectedTaskId && loadError
            ? `Task unavailable: ${selectedTaskId}`
            : "Select a task."}
        </p>
      </section>
    );
  }

  return (
    <section className="task-detail">
      <div className="section-heading">
        <div>
          <h2>Task</h2>
          <span className="mono">{task.id}</span>
        </div>
        <div className="button-row">
          <button className="icon-button primary" type="button" onClick={onOpenOrchestrator}>
            Agent
          </button>
          <button className="icon-button" type="button" onClick={onBackToBoard}>
            Board
          </button>
          <span className="badge">{task.status}</span>
        </div>
      </div>
      <ContractInvalidBanner task={task} detail={detail} events={events} />
      <div className="task-product-surface">
        <TaskRunSummaryPanel
          detail={detail}
          onOpenProjection={onOpenProjection}
          task={task}
        />
      </div>
      <div className="tab-row">
        {DETAIL_TABS.map((item) => (
          <button
            className={`tab-button ${tab === item ? "active" : ""}`}
            key={item}
            type="button"
            onClick={() => setTab(item)}
          >
            {item}
          </button>
        ))}
      </div>
      <div className="detail-body">
        {tab === "Timeline" ? (
          <ExecutionRoutePanel
            error={timelineError}
            loading={timelineLoading}
            route={timelineRoute}
            traceId={timelineTraceId}
            onOpenProjection={onOpenProjection}
          />
        ) : null}
        {tab === "Workbench" ? (
          <TaskWorkbenchPanel
            detail={detail}
            diff={diff}
            events={taskEvents.slice(0, 20)}
            onOpenProjection={onOpenProjection}
            task={task}
          />
        ) : null}
        {tab === "Overview" ? (
          <>
            <dl className="detail-grid">
              <dt>Title</dt>
              <dd>{task.title || "-"}</dd>
              <dt>Phase</dt>
              <dd>{task.phase ?? "-"}</dd>
              <dt>Assignee</dt>
              <dd>{task.assigned_to || "-"}</dd>
              <dt>Trace</dt>
              <dd>
                <EvidenceLink
                  id={detail?.links?.trace || detail?.trace_id || ""}
                  kind="trace"
                  onOpen={onOpenProjection}
                />
              </dd>
              <dt>Candidate</dt>
              <dd>
                <EvidenceLink
                  id={detail?.links?.candidate || ""}
                  kind="candidate"
                  onOpen={onOpenProjection}
                />
              </dd>
              <dt>Fanout</dt>
              <dd>
                <EvidenceLink
                  id={detail?.links?.fanout || ""}
                  kind="fanout"
                  onOpen={onOpenProjection}
                />
              </dd>
              <dt>Workdir</dt>
              <dd>{detail?.role_instance || task.assigned_to || "-"}</dd>
              <dt>Blocked By</dt>
              <dd>{task.blocked_by?.length ? task.blocked_by.join(", ") : "-"}</dd>
              <dt>Skills</dt>
              <dd>{task.skills_required?.length ? task.skills_required.join(", ") : "-"}</dd>
            </dl>
            <TaskEditPanel
              actionReady={actionReady}
              actionState={actionState}
              detail={detail}
              onUpdate={(payload) => onAction("update-task", payload)}
              task={task}
            />
            <TaskEffectiveSkillsPanel
              skillsSummary={skillsSummary}
              task={task}
            />
            <ArtifactLedgerPanel detail={detail} />
            <AssignmentIntentPanel
              actionReady={actionReady}
              actionState={actionState}
              onPropose={(payload) => onAction("assignment-propose", payload)}
              task={task}
            />
            <div className="action-row">
              <button className="icon-button primary" type="button" onClick={onOpenOrchestrator}>
                Open Agent
              </button>
              {["dispatch-task", "request-verify", "request-review", "ship-candidate"].map((action) => (
                <button className="icon-button" key={action} type="button" onClick={() => onAction(action)}>
                  {action}
                </button>
              ))}
            </div>
            {actionResult ? (
              <div className="notice">
                <span className="mono">{actionResult.status}</span> {actionResult.reason}
              </div>
            ) : null}
          </>
        ) : null}
        {tab === "Contract" ? <PreBlock value={detail?.contract ?? task} /> : null}
        {tab === "Briefing" ? (
          detail?.briefing.text ? <pre className="text-block">{detail.briefing.text}</pre> : <p className="empty-text">No briefing projection.</p>
        ) : null}
        {tab === "Events" ? <EventTable events={taskEvents} compact /> : null}
        {tab === "Git" ? (
          <>
            <PreBlock value={detail?.git ?? {}} />
            {diff?.files.length ? (
              <div className="compact-list">
                {diff.files.map((file) => <span className="mono" key={file}>{file}</span>)}
              </div>
            ) : null}
            {diff?.error ? <p className="empty-text">{diff.error}</p> : null}
            {diff?.diff ? <pre className="text-block diff-block">{diff.diff}</pre> : null}
          </>
        ) : null}
        {tab === "Verify" ? <PreBlock value={detail?.verify ?? {}} /> : null}
        {tab === "Review" ? <PreBlock value={detail?.review ?? {}} /> : null}
        {tab === "Diagnostics" ? <PreBlock value={detail?.diagnostics ?? {}} /> : null}
      </div>
    </section>
  );
}


function TaskRunSummaryPanel({
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
  const actorLabel = [
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
        {completedCount ? <span className="badge badge-ok">{completedCount} done</span> : null}
        {changedCount ? <span className="badge badge-muted task-run-files-badge">{changedCount} files</span> : null}
      </div>
      {latestMessage ? <p className="task-run-latest">{latestMessage}</p> : null}
    </section>
  );
}


function HandoffSummaryPanel({ detail }: { detail: TaskDetailModel | null }) {
  const summary = asRecord(detail?.handoff_summary);
  const owner = asRecord(summary.owner);
  const completed = asRecordArray(summary.completed).slice(0, 4);
  const missing = asRecordArray(summary.missing_evidence).slice(0, 4);
  const blockers = asStringArray(summary.blockers).slice(0, 4);
  const nextAction = textValue(summary.next_required_action);
  const nextEvent = textValue(summary.next_required_event);
  const changedFiles = asStringArray(summary.changed_files).slice(0, 4);

  return (
    <section className="subsection handoff-summary-panel">
      <div className="inline-heading">
        <h3>Handoff Summary</h3>
        <span className={`badge badge-${handoffTone(summary)}`}>
          {textValue(summary.current_state) || "unknown"}
        </span>
      </div>
      <dl className="detail-grid compact-detail-grid task-run-grid">
        <dt>owner</dt>
        <dd>{textValue(owner.role) || "-"} {textValue(owner.instance_id) ? <span className="mono">{textValue(owner.instance_id)}</span> : null}</dd>
        <dt>stage</dt>
        <dd>{textValue(summary.current_stage) || "-"}</dd>
        <dt>next_event</dt>
        <dd className="mono">{nextEvent || "-"}</dd>
        <dt>next_action</dt>
        <dd>{nextAction || "-"}</dd>
      </dl>
      {completed.length ? (
        <div className="compact-list">
          {completed.map((item, index) => (
            <span className="handoff-line" key={`done-${index}`}>
              <span className="badge badge-ok">done</span>
              <span>{textValue(item.summary || item.kind || item.type)}</span>
            </span>
          ))}
        </div>
      ) : null}
      {missing.length || blockers.length ? (
        <div className="compact-list">
          {missing.map((item, index) => (
            <span className="handoff-line" key={`missing-${index}`}>
              <span className="badge badge-warn">missing</span>
              <span>{textValue(item.message || item.kind || item.expected)}</span>
            </span>
          ))}
          {blockers.map((item) => (
            <span className="handoff-line" key={`blocker-${item}`}>
              <span className="badge badge-err">blocker</span>
              <span>{item}</span>
            </span>
          ))}
        </div>
      ) : null}
      {changedFiles.length ? (
        <div className="compact-list handoff-files">
          {changedFiles.map((file) => <span className="mono" key={file}>{file}</span>)}
        </div>
      ) : null}
      {!completed.length && !missing.length && !blockers.length && !nextAction ? (
        <p className="empty-text">No handoff summary yet.</p>
      ) : null}
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
  const poolRows = skillsSummary?.pool ?? [];
  return [...required].sort((left, right) => left.localeCompare(right)).map((skill) => {
    const loaded = findTaskSkillRecord(loadedRows, task, skill);
    const lock = findTaskSkillRecord(lockRows, task, skill);
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


function TaskEditPanel({
  actionReady,
  actionState,
  detail,
  onUpdate,
  task,
}: {
  actionReady: boolean;
  actionState: string;
  detail: TaskDetailModel | null;
  onUpdate: (payload: Record<string, unknown>) => void;
  task: Task;
}) {
  const [draft, setDraft] = useState<TaskEditDraft>(() => taskEditDraft(task, detail));

  useEffect(() => {
    setDraft(taskEditDraft(task, detail));
  }, [detail, task]);

  const update = (patch: Partial<TaskEditDraft>) => setDraft((current) => ({ ...current, ...patch }));
  const dirty = JSON.stringify(draft) !== JSON.stringify(taskEditDraft(task, detail));

  function saveTaskEdit() {
    onUpdate({
      title: draft.title,
      status: draft.status,
      priority: Number(draft.priority || 3),
      assigned_to: draft.assignedTo.trim(),
      blocked_reason: draft.blockedReason,
      blocked_by: csvList(draft.blockedBy),
      skills_required: csvList(draft.skills),
      contract: {
        behavior: draft.behavior,
        verification: draft.verification,
        scope: csvList(draft.scope),
        exclusions: csvList(draft.exclusions),
        acceptance: draft.acceptance || "exit_code=0",
        rework_to: draft.reworkTo,
      },
    });
  }

  return (
    <div className="subsection task-edit-panel">
      <div className="inline-heading">
        <h3>Maintenance</h3>
        <span className="muted">{actionReady ? "controlled action" : actionState}</span>
      </div>
      <div className="task-edit-grid">
        <label>
          <span>Title</span>
          <input className="filter-input" value={draft.title} onChange={(event) => update({ title: event.target.value })} />
        </label>
        <label>
          <span>Status</span>
          <select className="filter-input" value={draft.status} onChange={(event) => update({ status: event.target.value })}>
            {["backlog", "in_progress", "review", "testing", "blocked", "done", "cancelled"].map((status) => (
              <option key={status} value={status}>{status}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Priority</span>
          <select className="filter-input" value={draft.priority} onChange={(event) => update({ priority: event.target.value })}>
            {[0, 1, 2, 3, 4, 5].map((priority) => <option key={priority} value={String(priority)}>P{priority}</option>)}
          </select>
        </label>
        <label>
          <span>Assignee</span>
          <input className="filter-input" value={draft.assignedTo} onChange={(event) => update({ assignedTo: event.target.value })} />
        </label>
        <label>
          <span>Skills</span>
          <input className="filter-input" value={draft.skills} onChange={(event) => update({ skills: event.target.value })} />
        </label>
        <label>
          <span>Blocked By</span>
          <input className="filter-input" value={draft.blockedBy} onChange={(event) => update({ blockedBy: event.target.value })} />
        </label>
        <label className="wide-field">
          <span>Blocked Reason</span>
          <input className="filter-input" value={draft.blockedReason} onChange={(event) => update({ blockedReason: event.target.value })} />
        </label>
        <label className="wide-field">
          <span>Behavior</span>
          <textarea className="textarea-input compact-textarea" value={draft.behavior} onChange={(event) => update({ behavior: event.target.value })} />
        </label>
        <label className="wide-field">
          <span>Verification</span>
          <textarea className="textarea-input compact-textarea" value={draft.verification} onChange={(event) => update({ verification: event.target.value })} />
        </label>
        <label>
          <span>Scope</span>
          <input className="filter-input" value={draft.scope} onChange={(event) => update({ scope: event.target.value })} />
        </label>
        <label>
          <span>Exclusions</span>
          <input className="filter-input" value={draft.exclusions} onChange={(event) => update({ exclusions: event.target.value })} />
        </label>
        <label>
          <span>Acceptance</span>
          <input className="filter-input" value={draft.acceptance} onChange={(event) => update({ acceptance: event.target.value })} />
        </label>
        <label>
          <span>Rework To</span>
          <input className="filter-input" value={draft.reworkTo} onChange={(event) => update({ reworkTo: event.target.value })} />
        </label>
      </div>
      <div className="action-row flush-action-row">
        <button className="icon-button primary" disabled={!actionReady || !dirty} type="button" onClick={saveTaskEdit}>
          Save Task
        </button>
        <button className="icon-button" disabled={!dirty} type="button" onClick={() => setDraft(taskEditDraft(task, detail))}>
          Reset
        </button>
      </div>
    </div>
  );
}


function AssignmentIntentPanel({
  actionReady,
  actionState,
  onPropose,
  task,
}: {
  actionReady: boolean;
  actionState: string;
  onPropose: (payload: Record<string, unknown>) => void;
  task: Task;
}) {
  const [role, setRole] = useState(task.assigned_to || "");
  const [backend, setBackend] = useState("");
  const [channelId, setChannelId] = useState("");
  const [supervisor, setSupervisor] = useState("");
  const [reason, setReason] = useState("operator assignment intent");

  useEffect(() => {
    setRole(task.assigned_to || "");
  }, [task.id, task.assigned_to]);

  const hasIntent = Boolean(
    role.trim() || backend.trim() || channelId.trim() || supervisor.trim(),
  );

  function propose() {
    onPropose({
      task_id: task.id,
      role: role.trim() || undefined,
      backend: backend.trim() || undefined,
      channel_id: channelId.trim() || undefined,
      supervisor: supervisor.trim() || undefined,
      reason: reason.trim() || "operator assignment intent",
      source: "web-assignment-intent",
    });
  }

  return (
    <div className="subsection task-edit-panel">
      <div className="inline-heading">
        <h3>Assignment Intent</h3>
        <span className="muted">{actionReady ? "proposal only" : actionState}</span>
      </div>
      <div className="task-edit-grid">
        <label>
          <span>Role</span>
          <input className="filter-input" value={role} onChange={(event) => setRole(event.target.value)} />
        </label>
        <label>
          <span>Backend</span>
          <select className="filter-input" value={backend} onChange={(event) => setBackend(event.target.value)}>
            <option value="">unchanged</option>
            {OPERATOR_BACKENDS.map((item) => (
              <option key={item.id} value={item.id}>{item.title}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Channel</span>
          <input className="filter-input" value={channelId} onChange={(event) => setChannelId(event.target.value)} />
        </label>
        <label>
          <span>Supervisor</span>
          <input className="filter-input" value={supervisor} onChange={(event) => setSupervisor(event.target.value)} />
        </label>
        <label className="wide-field">
          <span>Reason</span>
          <input className="filter-input" value={reason} onChange={(event) => setReason(event.target.value)} />
        </label>
      </div>
      <div className="action-row flush-action-row">
        <button className="icon-button primary" disabled={!actionReady || !hasIntent} type="button" onClick={propose}>
          Propose Assignment
        </button>
      </div>
    </div>
  );
}


function TaskWorkbenchPanel({
  detail,
  diff,
  events,
  onOpenProjection,
  task,
}: {
  detail: TaskDetailModel | null;
  diff: TaskDiff | null;
  events: EventRecord[];
  onOpenProjection: (kind: ProjectionKind, id: string) => void;
  task: Task;
}) {
  const workdir = (detail?.workdir ?? {}) as Record<string, unknown>;
  const workdirPath = textValue(workdir.project_path || workdir.workdir);
  const branch = textValue(workdir.branch || workdir.branch_or_ref || task.git?.branch);
  const traceId = detail?.links?.trace || detail?.trace_id || task.links?.trace || "";
  const candidateId = detail?.links?.candidate || task.links?.candidate || "";
  const fanoutId = detail?.links?.fanout || task.links?.fanout || "";
  const statusModel = (detail?.status_model ?? {}) as Record<string, unknown>;
  const evidenceModel = (detail?.evidence_model ?? {}) as Record<string, unknown>;
  const executionEvidence = (evidenceModel.execution ?? {}) as Record<string, unknown>;
  const interactionEvidence = (evidenceModel.interaction ?? {}) as Record<string, unknown>;
  const transcriptCount = Number(interactionEvidence.transcript_count ?? 0);
  const currentSession = (interactionEvidence.current_session ?? {}) as Record<string, unknown>;
  const sessionRows = [
    { key: "status", value: task.status },
    { key: "phase", value: task.phase || "-" },
    { key: "assignee", value: task.assigned_to || "-" },
    { key: "role_session", value: detail?.role_instance || "-" },
    { key: "agent_scope", value: `project + context:${task.id}` },
    { key: "workdir", value: workdirPath || "-" },
    { key: "branch", value: branch || "-" },
  ];
  const statusRows = [
    { key: "canonical", value: statusModel.canonical_task_status || "task.status" },
    { key: "source", value: statusModel.task_status_source || "TaskStore/EventWriter" },
    { key: "task_status", value: statusModel.task_status || task.status },
    {
      key: "run_done",
      value: statusModel.run_completed_implies_task_done === false ? "evidence only" : stringify(statusModel.run_completed_implies_task_done),
    },
    { key: "done_requires", value: statusModel.done_requires || "-" },
  ];
  const qualityRows = [
    { key: "verify", value: detail?.verify?.state || "-" },
    { key: "verify_events", value: detail?.verify?.event_count ?? 0 },
    { key: "review", value: detail?.review?.state || "-" },
    { key: "review_events", value: detail?.review?.event_count ?? 0 },
    { key: "diff_files", value: diff?.files.length ?? 0 },
    { key: "diff_range", value: diff?.range || "-" },
  ];
  const operatorRows = [
    { key: "events", value: executionEvidence.event_count ?? events.length },
    { key: "runs", value: executionEvidence.run_count ?? detail?.runs?.length ?? 0 },
    { key: "transcripts", value: transcriptCount },
    { key: "session", value: currentSession.status || "-" },
    { key: "transcript", value: interactionEvidence.transcript_truth || "interaction_evidence_only" },
  ];

  return (
    <div className="task-workbench">
      <div className="workbench-grid evidence-grid">
        <KeyValuePanel title="Session" rows={sessionRows} />
        <KeyValuePanel title="Status Model" rows={statusRows} />
        <div className="subsection key-panel">
          <h3>Evidence</h3>
          <dl className="detail-grid compact-detail-grid">
            <dt>trace</dt>
            <dd><EvidenceLink id={traceId} kind="trace" onOpen={onOpenProjection} /></dd>
            <dt>candidate</dt>
            <dd><EvidenceLink id={candidateId} kind="candidate" onOpen={onOpenProjection} /></dd>
            <dt>fanout</dt>
            <dd><EvidenceLink id={fanoutId} kind="fanout" onOpen={onOpenProjection} /></dd>
            <dt>blocked</dt>
            <dd>{task.blocked_reason || "-"}</dd>
          </dl>
        </div>
        <KeyValuePanel title="Verify / Review" rows={qualityRows} />
        <KeyValuePanel title="Operator Evidence" rows={operatorRows} />
      </div>
      <HandoffSummaryPanel detail={detail} />
      <div className="subsection">
        <div className="inline-heading">
          <h3>Recent Task Events</h3>
          <span className="muted">{events.length} events</span>
        </div>
        <EventTable events={events} compact />
      </div>
      <TaskExecutionPanel detail={detail} onOpenProjection={onOpenProjection} />
      {diff?.files.length ? (
        <div className="subsection">
          <div className="inline-heading">
            <h3>Changed Files</h3>
            <span className="muted">{diff.files.length} files</span>
          </div>
          <div className="compact-list">
            {diff.files.map((file) => <span className="mono" key={file}>{file}</span>)}
          </div>
        </div>
      ) : null}
      {detail?.diagnostics && !detail.diagnostics.empty ? (
        <div className="subsection">
          <h3>Diagnostics</h3>
          <PreBlock value={detail.diagnostics} />
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


function EvidenceLink({
  id,
  kind,
  onOpen,
}: {
  id: string;
  kind: ProjectionKind;
  onOpen: (kind: ProjectionKind, id: string) => void;
}) {
  if (!id) return <span>-</span>;
  return (
    <button className="link-button mono" type="button" onClick={() => onOpen(kind, id)}>
      {id}
    </button>
  );
}

