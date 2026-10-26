import type { Task } from "../src/api/types";
import { groupTasks, sortTasks, taskActivity } from "../src/components/kanban/taskViewModel.js";
function assert(condition: unknown, message: string): void { if (!condition) throw new Error(message); }
const task = (id: string, title: string, status: string, priority: number): Task => ({ id, title, status, priority, assigned_to: "", retry_count: 0, blocked_reason: "", phase: null, created_at: "2026-10-01T10:00:00Z" });
const tasks = [task("T-B", "Beta", "ready", 2), task("T-A", "Alpha", "blocked", 0), task("T-C", "Gamma", "cancelled", 1)];
assert(sortTasks(tasks, "title")[0].id === "T-A", "title ordering");
assert(sortTasks(tasks, "priority", true)[0].id === "T-B", "descending numeric priority ordering");
assert(tasks[0].id === "T-B", "sorting does not mutate canonical projection");
const groups = groupTasks(tasks, "status");
assert(groups.map((g) => g.id).join(",") === "ready,blocked,done", "cancelled remains in Done semantic column");
assert(groupTasks(tasks, "owner")[0].title === "Unassigned", "unassigned owner grouping");
assert(groupTasks(tasks, "none")[0].tasks.length === 3, "no grouping preserves all filtered tasks");
assert(taskActivity(tasks[0]) === tasks[0].created_at, "activity falls back to actual created time");
console.log("taskViewModel.test.ts OK");
