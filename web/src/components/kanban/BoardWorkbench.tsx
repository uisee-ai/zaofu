// BoardWorkbench + exclusive closure, extracted verbatim from App.tsx (P1 split).
import { search } from "../../api/client";
import type { ActionResponse, AgentSummary, FanoutSummary, Task } from "../../api/types";
import { BoardColumn } from "../../components/kanban/BoardColumn";
import { SpineHealthStrip } from "../../components/kanban/SpineHealthStrip";
import { BacklogRefsBadge, RouteSummaryStrip, WorkflowBadges } from "../../components/kanban/TaskCard";
import { BOARD_COLUMNS, isBoardColumnId, taskColumn } from "../../components/kanban/board";
import type { BoardColumnId } from "../../components/kanban/board";
import { contextBadgeTone, contextLabel, formatTokens } from "../../lib/format";
import { taskPriority, taskRiskBadge } from "../../lib/task-display";
import type { TaskTelemetry } from "../../lib/task-display";
import { List } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, PointerEvent } from "react";
import type { ViewMode } from "../../app/sharedTypes";
import { formatUsd, needsOperatorAttention } from "../../app/shared";

interface PointerDragState {
  taskId: string;
  pointerId: number;
  startX: number;
  startY: number;
  active: boolean;
  sourceStatus: BoardColumnId;
}


function emptyTaskTelemetry(): TaskTelemetry {
  return {
    attention: [],
    contextRatio: null,
    inputTokens: 0,
    outputTokens: 0,
    usd: 0,
    workerIds: [],
  };
}


function buildTaskTelemetry(agents: AgentSummary[]): Map<string, TaskTelemetry> {
  const byTask = new Map<string, TaskTelemetry>();
  for (const agent of agents) {
    const taskId = agent.task_id || agent.active_task || "";
    if (!taskId) continue;
    const current = byTask.get(taskId) ?? emptyTaskTelemetry();
    const nextContext = typeof agent.context_usage_ratio === "number"
      ? Math.max(current.contextRatio ?? 0, agent.context_usage_ratio)
      : current.contextRatio;
    byTask.set(taskId, {
      attention: [
        ...current.attention,
        ...(needsOperatorAttention(agent.attention_state) ? [agent.attention_state || "attention"] : []),
      ],
      contextRatio: nextContext,
      inputTokens: current.inputTokens + (agent.cost?.input_tokens ?? 0),
      outputTokens: current.outputTokens + (agent.cost?.output_tokens ?? 0),
      usd: current.usd + (agent.cost?.usd ?? 0),
      workerIds: [...current.workerIds, agent.instance_id],
    });
  }
  return byTask;
}


export function BoardWorkbench({
  actionReady,
  actionResult,
  actionState,
  activeFanouts,
  agents,
  assignees,
  assigneeFilter,
  filteredTasks,
  mutationEnabled,
  onMoveTaskStatus,
  onOpenFanout,
  onOpenTask,
  onSaveToken,
  priorityFilter,
  projectId,
  quickFilter,
  selectedTaskId,
  setAssigneeFilter,
  setPriorityFilter,
  setQuickFilter,
  setSkillFilter,
  showTokenRow,
  skillFilter,
  skills,
  setStatusFilter,
  setTextFilter,
  setViewMode,
  statusFilter,
  tasksByColumn,
  textFilter,
  totalTaskCount,
  viewMode,
}: {
  actionReady: boolean;
  actionResult: ActionResponse | null;
  actionState: string;
  activeFanouts: FanoutSummary[];
  projectId?: string;
  agents: AgentSummary[];
  assignees: string[];
  assigneeFilter: string;
  filteredTasks: Task[];
  mutationEnabled: boolean;
  onMoveTaskStatus: (taskId: string, status: BoardColumnId) => void;
  onOpenFanout: (fanoutId: string) => void;
  onOpenTask: (taskId: string) => void;
  onSaveToken: (token: string) => void;
  priorityFilter: string;
  quickFilter: string;
  selectedTaskId: string | null;
  setAssigneeFilter: (value: string) => void;
  setPriorityFilter: (value: string) => void;
  setQuickFilter: (value: string) => void;
  setSkillFilter: (value: string) => void;
  showTokenRow: boolean;
  skillFilter: string;
  skills: string[];
  setStatusFilter: (value: string) => void;
  setTextFilter: (value: string) => void;
  setViewMode: (value: ViewMode) => void;
  statusFilter: string;
  tasksByColumn: Record<BoardColumnId, Task[]>;
  textFilter: string;
  totalTaskCount: number;
  viewMode: ViewMode;
}) {
  const [dragTaskId, setDragTaskId] = useState("");
  const [boardNotice, setBoardNotice] = useState("");
  const [tokenInput, setTokenInput] = useState("");
  const telemetryByTaskId = useMemo(() => buildTaskTelemetry(agents), [agents]);
  const pointerDragRef = useRef<PointerDragState | null>(null);
  const suppressClickTaskIdRef = useRef("");
  const taskById = useMemo(() => {
    const result = new Map<string, Task>();
    for (const task of filteredTasks) {
      result.set(task.id, task);
    }
    return result;
  }, [filteredTasks]);

  function resolveDropColumn(clientX: number, clientY: number): BoardColumnId | null {
    const element = document.elementFromPoint(clientX, clientY);
    const column = element?.closest<HTMLElement>(".board-column[data-column-id]");
    const status = column?.dataset.columnId;
    return isBoardColumnId(status) ? status : null;
  }

  function clearPointerDrag() {
    pointerDragRef.current = null;
    setDragTaskId("");
  }

  function handleTaskPointerDown(event: PointerEvent<HTMLElement>, taskId: string) {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    const task = taskById.get(taskId);
    if (!task) return;
    pointerDragRef.current = {
      taskId,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      active: false,
      sourceStatus: taskColumn(task),
    };
  }

  function handleBoardPointerMove(event: PointerEvent<HTMLElement>) {
    const drag = pointerDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const distance = Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY);
    if (!drag.active) {
      if (distance < 8) return;
      drag.active = true;
      setDragTaskId(drag.taskId);
    }
    event.preventDefault();
  }

  function handleBoardPointerUp(event: PointerEvent<HTMLElement>) {
    const drag = pointerDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    clearPointerDrag();
    if (!drag.active) return;
    suppressClickTaskIdRef.current = drag.taskId;
    window.setTimeout(() => {
      if (suppressClickTaskIdRef.current === drag.taskId) {
        suppressClickTaskIdRef.current = "";
      }
    }, 0);
    event.preventDefault();
    event.stopPropagation();
    const targetStatus = resolveDropColumn(event.clientX, event.clientY);
    if (!targetStatus || targetStatus === drag.sourceStatus) return;
    if (!actionReady) {
      setBoardNotice(`Cannot move ${drag.taskId}: board actions are ${actionState}.`);
      return;
    }
    setBoardNotice("");
    onMoveTaskStatus(drag.taskId, targetStatus);
  }

  function handleBoardPointerCancel(event: PointerEvent<HTMLElement>) {
    const drag = pointerDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    clearPointerDrag();
  }

  function handleTaskSelect(taskId: string) {
    if (suppressClickTaskIdRef.current === taskId) {
      suppressClickTaskIdRef.current = "";
      return;
    }
    onOpenTask(taskId);
  }

  function saveBoardToken() {
    onSaveToken(tokenInput);
    setTokenInput("");
    setBoardNotice("");
  }

  const moveActionResult = actionResult?.action === "update-task" ? actionResult : null;
  const activeFanout = totalTaskCount === 0 ? activeFanouts[0] : undefined;

  return (
    <>
      <SpineHealthStrip projectId={projectId} />
      <div className="section-heading">
        <div>
          <h2>Tasks</h2>
          <span className="muted">{filteredTasks.length} visible tasks</span>
        </div>
      </div>
      <div className="task-toolbar">
        <div className="task-toolbar-row">
          <button
            className={`segmented ${viewMode === "board" ? "active" : ""}`}
            type="button"
            onClick={() => setViewMode("board")}
          >
            Board
          </button>
          <button
            className={`segmented ${viewMode === "list" ? "active" : ""}`}
            type="button"
            onClick={() => setViewMode("list")}
          >
            List
          </button>
          <button
            className={`segmented ${quickFilter === "ready" ? "active" : ""}`}
            type="button"
            onClick={() => setQuickFilter("ready")}
          >
            Triage
          </button>
          <button
            className={`segmented ${quickFilter === "blocked" ? "active" : ""}`}
            type="button"
            onClick={() => setQuickFilter("blocked")}
          >
            Blocked
          </button>
          <button
            className={`segmented ${statusFilter === "testing" ? "active" : ""}`}
            type="button"
            onClick={() => setStatusFilter("testing")}
          >
            Verify
          </button>
          <input
            className="filter-input task-search-input"
            placeholder="filter tasks"
            value={textFilter}
            onChange={(event) => setTextFilter(event.target.value)}
          />
        </div>
        <div className="task-toolbar-row task-filter-row">
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="all">all status</option>
            {BOARD_COLUMNS.map((column) => (
              <option value={column.id} key={column.id}>{column.title}</option>
            ))}
          </select>
          <select value={assigneeFilter} onChange={(event) => setAssigneeFilter(event.target.value)}>
            <option value="all">all assignees</option>
            {assignees.map((assignee) => (
              <option value={assignee} key={assignee}>{assignee}</option>
            ))}
          </select>
          <select value={skillFilter} onChange={(event) => setSkillFilter(event.target.value)}>
            <option value="all">all skills</option>
            {skills.map((skill) => (
              <option value={skill} key={skill}>{skill}</option>
            ))}
          </select>
          <select value={priorityFilter} onChange={(event) => setPriorityFilter(event.target.value)}>
            <option value="all">all priority</option>
            {[0, 1, 2, 3, 4, 5].map((priority) => (
              <option value={String(priority)} key={priority}>P{priority}</option>
            ))}
          </select>
          <select
            aria-label="Task signal filter"
            value={quickFilter}
            onChange={(event) => setQuickFilter(event.target.value)}
          >
            <option value="focused">focused work</option>
            <option value="all">all tasks</option>
            <option value="ready">ready</option>
            <option value="blocked">blocked</option>
            <option value="failed">failed</option>
          </select>
        </div>
      </div>
      {!actionReady && mutationEnabled ? (
        <div className="notice board-action-notice">
          <div className="token-row">
            <span className="mono">board actions: {actionState}</span>
            {showTokenRow ? (
              <>
                <input
                  className="filter-input"
                  placeholder="action token"
                  type="password"
                  value={tokenInput}
                  onChange={(event) => setTokenInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") saveBoardToken();
                  }}
                />
                <button className="icon-button" type="button" onClick={saveBoardToken}>
                  Save
                </button>
                <button
                  className="icon-button"
                  type="button"
                  onClick={() => {
                    setTokenInput("");
                    onSaveToken("");
                  }}
                >
                  Clear
                </button>
              </>
            ) : null}
          </div>
        </div>
      ) : null}
      {boardNotice ? <div className="notice">{boardNotice}</div> : null}
      {moveActionResult ? (
        <div className={`notice ${moveActionResult.ok ? "notice-ok" : ""}`}>
          <span className="mono">{moveActionResult.status}</span> {moveActionResult.reason}
        </div>
      ) : null}
      {activeFanout ? (
        <div className="notice board-action-notice active-fanout-notice">
          <div>
            <span className="mono">{activeFanout.topology || "fanout"}</span>{" "}
            is running without canonical delivery tasks on this board.
          </div>
          <button
            className="icon-button"
            type="button"
            onClick={() => onOpenFanout(activeFanout.fanout_id)}
          >
            Open Fanout
          </button>
        </div>
      ) : null}
      {viewMode === "board" ? (
        <div
          className="board-grid"
          style={{ "--board-column-count": BOARD_COLUMNS.length } as CSSProperties}
          onPointerCancel={handleBoardPointerCancel}
          onPointerMove={handleBoardPointerMove}
          onPointerUp={handleBoardPointerUp}
        >
          {BOARD_COLUMNS.map((column) => (
            <BoardColumn
              key={column.id}
              column={column}
              tasks={tasksByColumn[column.id]}
              telemetryByTaskId={telemetryByTaskId}
              selectedTaskId={selectedTaskId}
              dragTaskId={dragTaskId}
              onPointerDown={handleTaskPointerDown}
              onSelect={handleTaskSelect}
            />
          ))}
        </div>
      ) : (
        <TaskListView
          tasks={filteredTasks}
          selectedTaskId={selectedTaskId}
          telemetryByTaskId={telemetryByTaskId}
          onOpenTask={onOpenTask}
        />
      )}
    </>
  );
}


function TaskListView({
  tasks,
  telemetryByTaskId,
  selectedTaskId,
  onOpenTask,
}: {
  tasks: Task[];
  telemetryByTaskId: Map<string, TaskTelemetry>;
  selectedTaskId: string | null;
  onOpenTask: (taskId: string) => void;
}) {
  const [focusedTaskId, setFocusedTaskId] = useState(selectedTaskId || tasks[0]?.id || "");
  useEffect(() => {
    if (selectedTaskId && selectedTaskId !== focusedTaskId) {
      setFocusedTaskId(selectedTaskId);
      return;
    }
    if (!tasks.some((task) => task.id === focusedTaskId)) {
      setFocusedTaskId(tasks[0]?.id || "");
    }
  }, [focusedTaskId, selectedTaskId, tasks]);

  const selectedTask = tasks.find((task) => task.id === focusedTaskId) ?? tasks[0] ?? null;
  const selectedTelemetry = selectedTask ? telemetryByTaskId.get(selectedTask.id) : undefined;
  const totalTokens = (selectedTelemetry?.inputTokens ?? 0) + (selectedTelemetry?.outputTokens ?? 0);
  const selectedRisk = selectedTask ? taskRiskBadge(selectedTask, selectedTelemetry) : null;

  return (
    <div className="task-list-layout">
      <section className="subsection task-list-panel">
        <div className="inline-heading">
          <h3>Task List</h3>
          <span className="muted">{tasks.length} tasks</span>
        </div>
        <div className="task-list-view" role="list">
          {tasks.map((task) => {
            const telemetry = telemetryByTaskId.get(task.id);
            const total = (telemetry?.inputTokens ?? 0) + (telemetry?.outputTokens ?? 0);
            const risk = taskRiskBadge(task, telemetry);
            return (
              <button
                className={`task-list-row ${task.id === selectedTask?.id ? "active" : ""}`}
                key={task.id}
                type="button"
                onClick={() => setFocusedTaskId(task.id)}
                role="listitem"
              >
                <span className="mono">{task.id}</span>
                <span>{task.title || "-"}</span>
                <span className="muted">{task.status}</span>
                <span className={`badge badge-${risk.tone}`}>{risk.label}</span>
                <span className={`badge badge-${contextBadgeTone(telemetry?.contextRatio)}`}>
                  {contextLabel(telemetry?.contextRatio)}
                </span>
                <span className="badge badge-muted">tok {formatTokens(total)}</span>
                <span className="task-route-list-cell">{task.route_summary?.summary || "-"}</span>
              </button>
            );
          })}
        </div>
      </section>
      <aside className="subsection task-inspector" aria-label="Task inspector">
        <div className="inline-heading">
          <h3>Inspector</h3>
          {selectedTask ? (
            <button className="icon-button" type="button" onClick={() => onOpenTask(selectedTask.id)}>
              Open Detail
            </button>
          ) : null}
        </div>
        {selectedTask ? (
          <>
            <div className="task-inspector-title">
              <span className="mono">{selectedTask.id}</span>
              <strong>{selectedTask.title || "(untitled)"}</strong>
            </div>
            <div className="badge-row">
              <span className={`badge badge-${selectedRisk?.tone ?? "muted"}`}>{selectedRisk?.label}</span>
              <WorkflowBadges task={selectedTask} />
              <span className={`badge badge-${contextBadgeTone(selectedTelemetry?.contextRatio)}`}>
                {contextLabel(selectedTelemetry?.contextRatio)}
              </span>
              <span className="badge badge-muted">tokens {formatTokens(totalTokens)}</span>
            </div>
            <RouteSummaryStrip route={selectedTask.route_summary} />
            <dl className="key-value-grid compact-kv">
              <dt>Status</dt>
              <dd>{selectedTask.status}</dd>
              <dt>Priority</dt>
              <dd>P{taskPriority(selectedTask)}</dd>
              <dt>Assignee</dt>
              <dd>{selectedTask.assigned_to || "-"}</dd>
              <dt>Workers</dt>
              <dd>{selectedTelemetry?.workerIds.join(", ") || "-"}</dd>
              <dt>Input tokens</dt>
              <dd>{formatTokens(selectedTelemetry?.inputTokens)}</dd>
              <dt>Output tokens</dt>
              <dd>{formatTokens(selectedTelemetry?.outputTokens)}</dd>
              <dt>Cost</dt>
              <dd>{formatUsd(selectedTelemetry?.usd)}</dd>
              <dt>Latest event</dt>
              <dd>{selectedTask.latest_event?.type || "-"}</dd>
            </dl>
            {selectedTask.blocked_reason ? (
              <div className="notice compact-error">{selectedTask.blocked_reason}</div>
            ) : null}
            <div className="subsection compact-subsection">
              <div className="inline-heading">
                <h3>Evidence</h3>
                <span className="muted">{selectedTask.evidence_badges?.length ?? 0}</span>
              </div>
              <div className="badge-row">
                {selectedTask.evidence_badges?.length ? selectedTask.evidence_badges.map((badge) => (
                  <span className={`badge badge-${badge.tone}`} key={`${selectedTask.id}-${badge.kind}-${badge.label}`}>
                    {badge.label}
                  </span>
                )) : <span className="muted">No evidence badges.</span>}
                <BacklogRefsBadge task={selectedTask} />
              </div>
            </div>
          </>
        ) : (
          <p className="empty-text">No task selected.</p>
        )}
      </aside>
    </div>
  );
}


