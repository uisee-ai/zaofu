// Kanban 列模型 —— 从 App.tsx 抽出(WEB-KANBAN-EXTRACT 第一片,docs/design/67 §4.1)。
// 纯分类逻辑,只依赖 api 的 Task 类型;App.tsx 与未来 <KanbanBoard>/<TaskCard> 共享。
import type { Task } from "../../api/types";

export const BOARD_COLUMNS = [
  { id: "ready", title: "Todo", tone: "brand" },
  { id: "in_progress", title: "In Progress", tone: "warn" },
  { id: "testing", title: "Verify", tone: "info" },
  { id: "blocked", title: "Blocked", tone: "err" },
  { id: "done", title: "Done", tone: "done" },
] as const;

export type BoardColumnId = (typeof BOARD_COLUMNS)[number]["id"];
export type BoardColumnConfig = (typeof BOARD_COLUMNS)[number];

export function isBoardColumnId(value: unknown): value is BoardColumnId {
  return typeof value === "string" && BOARD_COLUMNS.some((column) => column.id === value);
}

export function activeWorkflowColumn(task: Task): BoardColumnId {
  const role = (task.assigned_to || "").split(/[-_.]/)[0]?.toLowerCase() ?? "";
  const phase = (task.phase || "").toLowerCase();
  if (
    ["review", "test", "verify", "verifier", "judge", "qa"].includes(role)
    || [
      "build_done",
      "static_gate_passed",
      "review_requested",
      "review_approved",
      "test_running",
      "test_passed",
      "judge_running",
      "judge_passed",
    ].includes(phase)
  ) {
    return "testing";
  }
  return "in_progress";
}

export function taskColumn(task: Task): BoardColumnId {
  if (isBoardColumnId(task.kanban_column)) return task.kanban_column;
  if (["backlog", "ready", "todo", "planned", "pending"].includes(task.status)) return "ready";
  if (["review", "testing", "verify", "verifying", "judge"].includes(task.status)) return "testing";
  if (["done", "cancelled", "superseded", "archived"].includes(task.status)) return "done";
  if (task.status === "blocked") return "blocked";
  if (task.status === "in_progress") return activeWorkflowColumn(task);
  return "ready";
}
