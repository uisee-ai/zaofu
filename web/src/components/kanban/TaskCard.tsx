// Kanban TaskCard + 卡内子组件 —— 从 App.tsx 抽出(WEB-KANBAN-EXTRACT slice 3,docs/design/67 §4.1)。
// 纯展示组件:消费 board 列模型 + lib 的 format/task-display helper,只读 projection,不持 truth。
import type { CSSProperties, PointerEvent } from "react";
import type { Task } from "../../api/types";
import { taskColumn } from "./board";
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

// actor (assigned_to) 头一个 `-` 前缀作 role,role → 颜色 token。
// Task 没带 backend 字段,但 role 已足够告诉 operator 在做什么类型的活,
// 而且 role 与 backend 在大多数项目里有强相关(arch/critic 多走 codex,
// dev/test 跨 backend)。Backend 维度若以后真要可视化,fanout.run_id /
// route_summary 里会有 provider 信息可挖。
const ROLE_COLORS: Record<string, string> = {
  arch: "oklch(0.62 0.16 255)",     // brand 蓝
  critic: "oklch(0.65 0.17 35)",    // 暖橙
  dev: "oklch(0.65 0.16 145)",      // 绿
  review: "oklch(0.60 0.18 310)",   // 紫
  test: "oklch(0.66 0.15 200)",     // 青
  judge: "oklch(0.60 0.17 55)",     // 金
};
function actorRoleColor(assigned: string | null | undefined): string | null {
  if (!assigned) return null;
  const role = assigned.split(/[-_.]/)[0]?.toLowerCase() ?? "";
  return ROLE_COLORS[role] ?? null;
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
  onSelect,
}: {
  task: Task;
  dragging: boolean;
  onPointerDown: (event: PointerEvent<HTMLElement>, taskId: string) => void;
  selected: boolean;
  onSelect: (taskId: string) => void;
}) {
  const currentColumn = taskColumn(task);
  const priority = taskPriority(task);
  const actorLabel = taskActorLabel(task);
  const age = latestEventAge(task);
  const attention = taskAttentionBadge(task);
  const effectiveTerminal = taskIsEffectivelyTerminal(task);
  const fanout = task.fanout;

  return (
    <article
      className={`task-card status-${currentColumn} priority-${priority} ${selected ? "selected" : ""} ${dragging ? "dragging" : ""}`}
      data-task-id={task.id}
      data-task-status={currentColumn}
      data-fanout-id={fanout?.fanout_id ?? undefined}
      onPointerDown={(event) => onPointerDown(event, task.id)}
    >
      <button className="task-open" type="button" onClick={() => onSelect(task.id)}>
        <span className="task-id mono">{task.id}</span>
        <span className="task-title">{task.title || "(untitled)"}</span>
        {effectiveTerminal ? (
          <span className="task-chip-row">
            <span className={`badge badge-${taskTerminalTone(task)}`}>{taskTerminalOutcome(task)}</span>
          </span>
        ) : attention.label ? (
          <span className="task-chip-row"><span className={`badge badge-${attention.tone}`}>{attention.label}</span></span>
        ) : null}
        {attention.label && task.blocked_reason ? <span className="blocked-note">{task.blocked_reason}</span> : null}
        <span className="task-footer">
          {actorLabel ? (
            <span className="task-assignee">
              {(() => {
                const roleColor = actorRoleColor(task.assigned_to);
                return roleColor ? (
                  <span
                    className="actor-dot"
                    style={{ background: roleColor } as CSSProperties}
                    aria-hidden
                  />
                ) : null;
              })()}
              {actorLabel}
            </span>
          ) : null}
          {!effectiveTerminal ? <span className={`priority-pill priority-${priority}`}>P{priority}</span> : null}
          {age !== "-" ? <span className="task-age">{age}</span> : null}
        </span>
      </button>
    </article>
  );
}
