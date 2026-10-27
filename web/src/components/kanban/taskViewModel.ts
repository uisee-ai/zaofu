import type { Task } from "../../api/types";
import { taskPriority } from "../../lib/task-display.js";
import { BOARD_COLUMNS, taskColumn } from "./board.js";
export type TaskSort = "activity" | "title" | "priority" | "owner" | "status" | "phase";
export type TaskGroup = "status" | "owner" | "priority" | "none";
export function taskActivity(task: Task): string { return task.task_card?.activity.at || task.latest_event?.ts || task.created_at || ""; }
export function taskOwner(task: Task): string { return task.task_card?.owner.actor_id || task.assigned_to || "Unassigned"; }
export function taskPhase(task: Task): string { return task.task_card?.current_stage || task.workflow_phase || task.phase || "—"; }
export function taskStatus(task: Task): string { return task.task_card?.lifecycle.display_status || task.display_status || task.status; }
export function sortTasks(tasks: Task[], sort: TaskSort, descending = false): Task[] {
  const value = (task: Task): string | number => {
    if (sort === "priority") return taskPriority(task);
    if (sort === "activity") return Date.parse(taskActivity(task)) || 0;
    if (sort === "owner") return taskOwner(task);
    if (sort === "status") return BOARD_COLUMNS.findIndex((column) => column.id === taskColumn(task));
    if (sort === "phase") return taskPhase(task);
    return task.title || task.id;
  };
  return [...tasks].sort((a, b) => {
    const av = value(a), bv = value(b);
    const result = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
    return (descending ? -result : result) || a.id.localeCompare(b.id);
  });
}
export function groupTasks(tasks: Task[], group: TaskGroup): Array<{ id: string; title: string; tasks: Task[] }> {
  if (group === "none") return [{ id: "all", title: "All tasks", tasks }];
  const groups = new Map<string, Task[]>();
  for (const task of tasks) {
    const key = group === "status" ? taskColumn(task) : group === "owner" ? taskOwner(task) : `P${taskPriority(task)}`;
    groups.set(key, [...(groups.get(key) || []), task]);
  }
  const keys = group === "status" ? BOARD_COLUMNS.map((c) => c.id).filter((key) => groups.has(key)) : [...groups.keys()].sort();
  return keys.map((id) => ({ id, title: group === "status" ? BOARD_COLUMNS.find((c) => c.id === id)!.title : id, tasks: groups.get(id)! }));
}
