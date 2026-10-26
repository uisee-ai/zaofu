import { Clock3, SignalLow, SignalMedium, SignalHigh, OctagonAlert, UserRound } from "lucide-react";
// Kanban TaskCard + 卡内子组件 —— 从 App.tsx 抽出(WEB-KANBAN-EXTRACT slice 3,docs/design/67 §4.1)。
// 纯展示组件:消费 board 列模型 + lib 的 format/task-display helper,只读 projection,不持 truth。
import type { PointerEvent } from "react";
import type { Task } from "../../api/types";
import { taskColumn } from "./board";
import { useUiPreferences } from "../../app/UiPreferences";
import {
  taskPriority,
  taskActorLabel,
  taskRiskBadge,
  taskIsEffectivelyTerminal,
  taskTerminalOutcome,
  taskTerminalTone,
  latestEventAge,
  backlogRefsState,
  routeStatusTone,
  type TaskTelemetry,
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
  task,
  dragging,
  onPointerDown,
  selected,
  telemetry,
  onSelect,
}: {
  task: Task;
  dragging: boolean;
  onPointerDown: (event: PointerEvent<HTMLElement>, taskId: string) => void;
  selected: boolean;
  telemetry?: TaskTelemetry;
  onSelect: (taskId: string) => void;
}) {
  const { t } = useUiPreferences();
  const currentColumn = taskColumn(task);
  const priority = taskPriority(task);
  const PriorityIcon = priority === 0 ? OctagonAlert : priority === 1 ? SignalHigh : priority === 2 ? SignalMedium : SignalLow;
  const actorLabel = taskActorLabel(task);
  const age = latestEventAge(task);
  const risk = taskRiskBadge(task, telemetry);
  const terminal = taskIsEffectivelyTerminal(task);
  return (
    <article className={`task-card status-${currentColumn} ${selected ? "selected" : ""} ${dragging ? "dragging" : ""}`}
      data-task-id={task.id} data-task-status={currentColumn} data-fanout-id={task.fanout?.fanout_id}
      onPointerDown={(event) => onPointerDown(event, task.id)}>
      <button className="task-open" type="button" onClick={() => onSelect(task.id)}>
        {Boolean(task.skills_required?.length) && <span className="task-labels">{task.skills_required!.slice(0, 2).map((skill) => <span className="task-label" key={skill}><i aria-hidden="true" />{skill}</span>)}</span>}
        <span className="task-title">{task.title || task.id}</span>
        {(terminal || risk.label) && <span className="task-chip-row">{terminal
          ? <span className={`badge badge-${taskTerminalTone(task)}`}>{taskTerminalOutcome(task)}</span>
          : <span className={`badge badge-${risk.tone}`}>{risk.label}</span>}</span>}
        {!terminal && task.blocked_reason && <span className="blocked-note" title={task.blocked_reason}>{task.blocked_reason}</span>}
        <span className="task-footer">
          <span className="task-id mono" title={task.id}>{task.id}</span>
          <span className="task-priority-icon" title={`${t("Priority")} P${priority}`} aria-label={`${t("Priority")} P${priority}`}><PriorityIcon size={13} aria-hidden="true" /></span>
          {age !== "-" && <span className="task-age"><Clock3 size={12} aria-hidden="true" />{age}</span>}
          <span className={`task-assignee-avatar ${actorLabel ? "assigned" : ""}`} title={actorLabel || t("Unassigned")} aria-label={actorLabel || t("Unassigned")}>
            {actorLabel ? actorLabel.replace(/^@/, "").split(/[-_ /]+/).slice(0, 2).map((word) => word[0]).join("").toUpperCase() : <UserRound size={12} aria-hidden="true" />}
          </span>
        </span>
      </button>
    </article>
  );
}
