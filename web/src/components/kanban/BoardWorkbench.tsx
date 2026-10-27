import { TaskToolbar } from "./TaskToolbar";
import { TaskViews } from "./TaskViews";
import { sortTasks } from "./taskViewModel";
import type { TaskSort, TaskGroup } from "./taskViewModel";
// BoardWorkbench + exclusive closure, extracted verbatim from App.tsx (P1 split).
import type { ActionResponse, AgentSummary, FanoutSummary, Task } from "../../api/types";
import { BoardColumn } from "../../components/kanban/BoardColumn";
import { BOARD_COLUMNS, isBoardColumnId, taskColumn } from "../../components/kanban/board";
import type { BoardColumnId } from "../../components/kanban/board";
import {
  selectTaskFocus,
  taskFocusForFilters,
} from "../../components/kanban/taskToolbarSelection";
import type { TaskFocus } from "../../components/kanban/taskToolbarSelection";
import type { TaskTelemetry } from "../../lib/task-display";
import { useMemo, useRef, useState } from "react";
import type { CSSProperties, PointerEvent } from "react";
import type { ViewMode } from "../../app/sharedTypes";
import { needsOperatorAttention } from "../../app/shared";

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
  projectName,
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
  onNewTask,
  onSaveToken,
  onUnlockSession,
  passcodeRequired,
  priorityFilter,
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
  textFilter,
  totalTaskCount,
  viewMode,
}: {
  projectName: string;
  actionReady: boolean;
  actionResult: ActionResponse | null;
  actionState: string;
  activeFanouts: FanoutSummary[];
  agents: AgentSummary[];
  assignees: string[];
  assigneeFilter: string;
  filteredTasks: Task[];
  mutationEnabled: boolean;
  onMoveTaskStatus: (taskId: string, status: BoardColumnId) => void;
  onOpenFanout: (fanoutId: string) => void;
  onOpenTask: (taskId: string) => void;
  onNewTask: () => void;
  onSaveToken: (token: string) => void;
  onUnlockSession: (passcode: string) => Promise<{ ok: boolean; reason?: string }>;
  passcodeRequired: boolean;
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
  textFilter: string;
  totalTaskCount: number;
  viewMode: ViewMode;
}) {
  const [sort, setSort] = useState<TaskSort>("activity");
  const [descending, setDescending] = useState(true);
  const [group, setGroup] = useState<TaskGroup>("status");
  const sortedTasks = useMemo(() => sortTasks(filteredTasks, sort, descending), [filteredTasks, sort, descending]);
  const tasksByColumn = useMemo(() => {
    const grouped = Object.fromEntries(BOARD_COLUMNS.map((column) => [column.id, [] as Task[]])) as Record<BoardColumnId, Task[]>;
    for (const task of sortedTasks) grouped[taskColumn(task)].push(task);
    return grouped;
  }, [sortedTasks]);
  const [dragTaskId, setDragTaskId] = useState("");
  const [boardNotice, setBoardNotice] = useState("");
  const [passcodeInput, setPasscodeInput] = useState("");
  const [tokenInput, setTokenInput] = useState("");
  const [unlocking, setUnlocking] = useState(false);
  const telemetryByTaskId = useMemo(() => buildTaskTelemetry(agents), [agents]);
  const activeTaskFocus = taskFocusForFilters(statusFilter, quickFilter);
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

  async function unlockBoardSession() {
    const passcode = passcodeInput.trim();
    if (!passcode) {
      setBoardNotice("Enter the web passcode to unlock board actions.");
      return;
    }
    setUnlocking(true);
    setBoardNotice("");
    try {
      const result = await onUnlockSession(passcode);
      if (result.ok) {
        setPasscodeInput("");
        return;
      }
      setBoardNotice(result.reason || "Unable to unlock board actions.");
    } catch (error) {
      setBoardNotice(error instanceof Error ? error.message : "Unable to unlock board actions.");
    } finally {
      setUnlocking(false);
    }
  }

  function handleTaskFocus(requested: TaskFocus) {
    const selection = selectTaskFocus(statusFilter, quickFilter, requested);
    setStatusFilter(selection.statusFilter);
    setQuickFilter(selection.quickFilter);
  }

  const moveActionResult = actionResult?.action === "update-task" ? actionResult : null;
  const activeFanout = totalTaskCount === 0 ? activeFanouts[0] : undefined;

  const accessControl = !actionReady && mutationEnabled ? (
        <details className="board-access"><summary>{actionState}</summary><div className="board-access-popover">
          <div className="token-row">
            <span className="mono">board actions: {actionState}</span>
            {passcodeRequired ? (
              <form
                className="token-row"
                onSubmit={(event) => {
                  event.preventDefault();
                  void unlockBoardSession();
                }}
              >
                <input
                  aria-label="Web passcode"
                  autoComplete="current-password"
                  className="filter-input"
                  disabled={unlocking}
                  placeholder="web passcode"
                  type="password"
                  value={passcodeInput}
                  onChange={(event) => setPasscodeInput(event.target.value)}
                />
                <button className="icon-button" disabled={unlocking} type="submit">
                  {unlocking ? "Unlocking…" : "Unlock"}
                </button>
              </form>
            ) : null}
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
        </div></details>
      ) : null;

  return (
    <>
      <TaskToolbar
        {...{ textFilter, setTextFilter, statusFilter, setStatusFilter, assigneeFilter, setAssigneeFilter, assignees,
          skillFilter, setSkillFilter, skills, priorityFilter, setPriorityFilter, quickFilter, setQuickFilter,
          viewMode, setViewMode, sort, setSort, descending, setDescending, group, setGroup, onNewTask }}
        activeFocus={activeTaskFocus} onFocus={handleTaskFocus} count={filteredTasks.length} projectName={projectName} accessControl={accessControl}
      />
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
              selectedTaskId={selectedTaskId}
              dragTaskId={dragTaskId}
              onPointerDown={handleTaskPointerDown}
              onSelect={handleTaskSelect}
              onNewTask={onNewTask}
            />
          ))}
        </div>
      ) : (
        <TaskViews
          tasks={sortedTasks} view={viewMode} group={group}
          selectedTaskId={selectedTaskId} telemetry={telemetryByTaskId}
          onOpenTask={onOpenTask} sort={sort} descending={descending}
          onSort={(next) => { if (next === sort) setDescending(!descending); else { setSort(next); setDescending(false); } }}
        />
      )}
    </>
  );
}
