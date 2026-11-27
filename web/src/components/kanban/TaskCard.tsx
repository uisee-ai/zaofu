import { Clock3, SignalLow, SignalMedium, SignalHigh, OctagonAlert, UserRound } from "lucide-react";
// Kanban TaskCard + 卡内子组件 —— 从 App.tsx 抽出(WEB-KANBAN-EXTRACT slice 3,docs/design/67 §4.1)。
// 纯展示组件:消费 board 列模型 + lib 的 format/task-display helper,只读 projection,不持 truth。
import type { PointerEvent } from "react";
import type { Task } from "../../api/types";
import { projectionPageQuery } from "../../app/pageCompatibility";
import { formatDateTime } from "../../lib/uiPreferences";
import { taskColumn } from "./board";
import { useUiPreferences } from "../../app/UiPreferences";
import {
  taskPriority,
  taskActorLabel,
  taskAttentionBadge,
  taskIsEffectivelyTerminal,
  taskTerminalOutcome,
  taskTerminalTone,
  latestEventAge,
  backlogRefsState,
  routeStatusTone,
} from "../../lib/task-display";

const PRE_BACKLOG_STAGES = new Set(["design", "design_critique", "backlog"]);

// 把路由 step 的长 worker 路径压成短可读 lane 名,完整保留在 title hover。
// 例:"zf-cli/dev-lane-0/dev-lane-0-PI-CORE-001/review-lane-0+" → "review-lane-0+"
function shortStepLabel(step: string): string {
  const last = step.split("/").pop() || step;
  // 砍 trailing task-id (e.g. "review-lane-0-PI-CORE-001" → "review-lane-0")
  return last.replace(/-[A-Z][A-Z0-9_-]*-\d+/, "");
}

// 后端原始 badge label 的中文/图标翻译(渐进:不识别的回退原样)。
function workflowBadgeTone(tone: string | undefined): string {
  if (["ok", "warn", "err", "muted", "info"].includes(tone || "")) return tone || "muted";
  return "muted";
}

export function WorkflowBadges({
  compact = false,
  task,
}: {
  compact?: boolean;
  task: Task;
}) {
  const badges = task.workflow_badges ?? task.workflow_projection?.badges ?? [];
  const lanes = task.verify_lanes ?? task.workflow_projection?.verify_lanes ?? [];
  const visible = badges.slice(0, compact ? 3 : 6);
  if (!visible.length && !lanes.length) {
    const phase = task.workflow_phase || task.phase || taskColumn(task);
    return <span className="badge badge-muted">phase {phase}</span>;
  }
  const laneTitle = lanes
    .map((lane) => `${lane.lane}: ${lane.state}${lane.event_type ? ` (${lane.event_type})` : ""}`)
    .join("\n");
  return (
    <>
      {visible.map((badge) => (
        <span
          className={`badge badge-${workflowBadgeTone(badge.tone)}`}
          key={`${task.id}-workflow-${badge.kind}-${badge.label}`}
          title={badge.label}
        >
          {badge.label}
        </span>
      ))}
      {lanes.length ? (
        <span className="badge badge-info" title={laneTitle}>
          lanes {lanes.length}
        </span>
      ) : null}
    </>
  );
}

export function BacklogRefsBadge({ task }: { task: Task }) {
  const contract = (task.contract ?? undefined) as Record<string, unknown> | undefined;
  // server returns task.contract directly; in the kanban list endpoint contract
  // may not be expanded — bail out silently rather than render a noisy 0/6.
  if (contract === undefined) return null;

  const { present, missing, total } = backlogRefsState(contract);
  const count = present.length;
  const assignedRoleish = (task.assigned_to || "").split("-")[0].toLowerCase();
  const stage = (task.phase ?? task.status ?? "").toLowerCase();
  const isPreBacklog =
    PRE_BACKLOG_STAGES.has(stage) ||
    assignedRoleish === "arch" ||
    assignedRoleish === "critic";

  let tone: "ok" | "warn" | "err" | "muted" = "ok";
  let label = `📋 ${count}/${total}`;
  if (count === 0 && isPreBacklog) {
    tone = "muted";
    label = "📋 pre-backlog";
  } else if (count < total) {
    tone = count === 0 ? "err" : "warn";
  }

  const title =
    count === total
      ? "All 6 required_backlog_refs populated"
      : isPreBacklog && count === 0
        ? "Task is pre-backlog stage; orchestrator will synthesize the 6 refs on design.critique.done verdict=approve"
        : `Missing required_backlog_refs:\n• ${missing.join("\n• ")}`;

  return (
    <span className={`badge badge-${tone}`} title={title}>
      {label}
    </span>
  );
}

export function RouteSummaryStrip({
  compact = false,
  route,
}: {
  compact?: boolean;
  route?: Task["route_summary"] | null;
}) {
  if (!route || route.empty || !route.summary) {
    return compact ? null : <p className="empty-text route-empty">No execution route yet.</p>;
  }
  const steps = route.summary.split(" -> ").filter(Boolean);
  return (
    <div className={`route-summary-strip ${compact ? "compact" : ""}`}>
      <span className={`badge badge-${routeStatusTone(route.status)}`}>
        {route.current_stage_label || route.status}
      </span>
      <span className="route-summary-flow">
        {steps.map((step, index) => (
          <span className="route-summary-step" key={`${step}-${index}`}>
            <span className="mono route-step-label" title={step}>{shortStepLabel(step)}</span>
            {index < steps.length - 1 ? <span className="route-arrow">→</span> : null}
          </span>
        ))}
      </span>
    </div>
  );
}

export function TaskCard({
  projectId,
  task,
  dragging,
  onPointerDown,
  selected,
  onSelect,
}: {
  projectId?: string;
  task: Task;
  dragging: boolean;
  onPointerDown: (event: PointerEvent<HTMLElement>, taskId: string) => void;
  selected: boolean;
  onSelect: (taskId: string) => void;
}) {
  const { t, preferences } = useUiPreferences();
  const currentColumn = taskColumn(task);
  const priority = taskPriority(task);
  const PriorityIcon = priority === 0 ? OctagonAlert : priority === 1 ? SignalHigh : priority === 2 ? SignalMedium : SignalLow;
  const actorLabel = taskActorLabel(task);
  const actorTitle = actorLabel
    ? `${t(task.assigned_to ? "Assignee" : "Required skill")} · ${actorLabel}`
    : t("Unassigned");
  const age = latestEventAge(task);
  const attention = taskAttentionBadge(task);
  const effectiveTerminal = taskIsEffectivelyTerminal(task);
  const execution = task.task_card?.execution;
  const stage = execution?.stage || "";
  const state = execution?.state || "";
  const phase = [stage, state !== stage && state].filter((name): name is string => Boolean(name))
    .map(name => t(name).replaceAll("_", " ")).join(" · ");
  const instances = effectiveTerminal ? [] : execution?.active_instances || [];
  const milestone = execution?.last_output || execution?.milestone;
  const waiting = !effectiveTerminal && (execution?.waiting_for || (currentColumn === "blocked" && task.blocked_reason));
  const runId = execution?.workflow_run_id;
  const run = projectId && runId ? projectionPageQuery(new URLSearchParams({ project: projectId }), "run", runId) : null;

  return (
    <article className={`task-card status-${currentColumn} ${selected ? "selected" : ""} ${dragging ? "dragging" : ""}`}
      data-task-id={task.id} data-task-status={currentColumn} data-fanout-id={task.fanout?.fanout_id}
      onPointerDown={(event) => onPointerDown(event, task.id)}>
      <button className="task-open" type="button" onClick={() => onSelect(task.id)}>
        <span className="task-title">{task.title || task.id}</span>
        {!effectiveTerminal && phase && <span className="card-execution-stage">{phase}</span>}
        {instances.length > 0 && <span className="card-execution-row">
          <span>{t("Active workers")}{instances.length > 1 && ` (${instances.length})`}</span>
          <span>{instances.slice(0, 2).map(member => <span className="card-worker" key={member.instance_id}
            title={[member.role, member.instance_id, member.attempt_id].filter(Boolean).join(" · ")}>
            {member.role && member.role !== member.instance_id && `${t(member.role)} · `}{member.instance_id}
          </span>)}{instances.length > 2 && `+${instances.length - 2}`}</span>
        </span>}
        {milestone && <span className="card-execution-row" data-testid="task-card-milestone">
          <span>{t(execution?.last_output ? "Latest output" : "Latest progress")}</span>
          <span>{milestone.label} <time dateTime={milestone.at}>{formatDateTime(milestone.at, preferences)}</time></span>
        </span>}
        {waiting && <span className="card-execution-row"><span>{t("Waiting for")}</span><span>{waiting}</span></span>}
        {(effectiveTerminal || (currentColumn !== "blocked" && attention.label)) && <span className="task-chip-row">{effectiveTerminal
          ? <span className={`badge badge-${taskTerminalTone(task)}`}>{taskTerminalOutcome(task)}</span>
          : <span className={`badge badge-${attention.tone}`}>{attention.label}</span>}</span>}
        <span className="task-footer">
          <span className="task-id mono" title={task.id}>{task.id}</span>
          <span className="task-priority-icon" title={`${t("Priority")} P${priority}`} aria-label={`${t("Priority")} P${priority}`}><PriorityIcon size={13} aria-hidden="true" /></span>
          {!milestone && age !== "-" && <span className="task-age" title={t("Last activity")}><Clock3 size={12} aria-hidden="true" />{age}</span>}
          <span className={`task-assignee-avatar ${actorLabel ? "assigned" : ""}`} title={actorTitle} aria-label={actorTitle}>
            {actorLabel ? actorLabel.replace(/^@/, "").split(/[-_ /]+/).slice(0, 2).map((word) => word[0]).join("").toUpperCase() : <UserRound size={12} aria-hidden="true" />}
          </span>
        </span>
      </button>
      {instances.length > 1 && <details className="card-attempts" onPointerDown={event => event.stopPropagation()}>
        <summary>{t("Current attempts")} ({instances.length})</summary>
        <ul>{instances.map(member => <li key={member.instance_id}>
          {member.role && `${t(member.role)} · `}{member.instance_id}
          {member.attempt_id && <code>{member.attempt_id}</code>}
        </li>)}</ul>
      </details>}
      {run && <a className="card-run-link" href={`?${run.params}`} title={runId}
        onPointerDown={event => event.stopPropagation()}>{t("View run")} ↗</a>}
    </article>
  );
}
