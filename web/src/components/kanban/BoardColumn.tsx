// Kanban 列容器 —— 从 App.tsx BoardWorkbench 抽出(WEB-KANBAN-EXTRACT slice 4,docs/design/67 §4.1)。
// 纯展示:列头 + task-list 渲染 TaskCard;拖拽 handler 由 BoardWorkbench 作 props 传入,
// commit 仍走 gate(doc 31)。本组件不持 truth、不做转换判断。
import type { PointerEvent } from "react";
import type { Task } from "../../api/types";
import type { TaskTelemetry } from "../../lib/task-display";
import type { BoardColumnConfig } from "./board";
import { TaskCard } from "./TaskCard";

export function BoardColumn({
  column,
  tasks,
  telemetryByTaskId,
  selectedTaskId,
  dragTaskId,
  onPointerDown,
  onSelect,
}: {
  column: BoardColumnConfig;
  tasks: Task[];
  telemetryByTaskId: Map<string, TaskTelemetry>;
  selectedTaskId: string | null;
  dragTaskId: string;
  onPointerDown: (event: PointerEvent<HTMLElement>, taskId: string) => void;
  onSelect: (taskId: string) => void;
}) {
  return (
    <section
      aria-label={`${column.title} column`}
      className={`board-column status-${column.id} tone-${column.tone} ${
        tasks.length ? "has-tasks" : "is-empty"
      } ${dragTaskId ? "drop-ready" : ""}`}
      data-column-id={column.id}
      data-task-count={tasks.length}
    >
      <div className="column-header">
        <span className="column-title">
          <span className="column-accent-dot" aria-hidden="true" />
          <h3>{column.title}</h3>
        </span>
        <span className="count">{tasks.length}</span>
      </div>
      <div className="task-list">
        {tasks.map((task) => (
          <TaskCard
            key={task.id}
            task={task}
            telemetry={telemetryByTaskId.get(task.id)}
            selected={task.id === selectedTaskId}
            dragging={task.id === dragTaskId}
            onPointerDown={onPointerDown}
            onSelect={onSelect}
          />
        ))}
      </div>
    </section>
  );
}
